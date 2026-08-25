from __future__ import annotations

import base64
import json
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
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


class NewType11FinalDashboard(Component):
    display_name = "11 Final Dashboard"
    description = "Queries the full dashboard after any loop Done signal."
    name = "NewType11FinalDashboard"
    icon = "ClipboardCheck"

    inputs = [DataInput(name="loop_done", display_name="Loop Done", required=True)]
    outputs = [Output(display_name="Result Message", name="result", method="build_result", types=["Message"])]

    def build_result(self) -> Message:
        try:
            payload = self._parse_payload(getattr(self, "loop_done", ""))
            self._db_config = dict(payload.get("db_config") or {})
            dashboard = self._query_dashboard()
            answer = self._build_answer(dashboard)
            chart_url = self._chart_data_url((dashboard.get("agents") or {}))
            self.status = {
                **payload,
                "component": "11_finalDashboard",
                "dashboard_data": dashboard,
                "dashboard_chart_url": chart_url,
                "answer_text": answer,
                "final": True,
            }
            return self._message(answer, chart_url)
        except Exception as exc:
            answer = f"## Final Dashboard\n\nDashboard refresh failed after loop completion.\n\nError: {exc}"
            self.status = {"ok": False, "component": "11_finalDashboard", "error": str(exc), "answer_text": answer}
            return Message(text=answer)

    def _query_dashboard(self) -> dict[str, Any]:
        if not self._has_db_config():
            raise ValueError("db_config is required from 10B Done output")
        agents = {
            "db_migration": self._migration_summary(),
            "sql_conversion": self._sql_conversion_summary(),
            "sql_tuning": self._sql_tuning_summary(),
            "sql_formatting": self._sql_formatting_summary(),
        }
        return {"ok": True, "agents": agents, "recommendation": self._recommendation(agents)}

    def _migration_summary(self) -> dict[str, Any]:
        table = self._qualify("NEXT_MIG_INFO")
        total = self._count(table)
        target = self._count(table, "UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y' AND STATUS IS NULL")
        pass_count = self._count(table, "UPPER(TRIM(NVL(STATUS, 'NULL'))) IN ('PASS', 'SUCCESS')")
        fail_count = self._count(table, "UPPER(TRIM(NVL(STATUS, 'NULL'))) = 'FAIL' OR UPPER(TRIM(NVL(STATUS, 'NULL'))) LIKE 'FAIL-%'")
        return self._stage_summary(
            agent="DB_MIGRATION",
            table=table,
            target_condition="USE_YN='Y' AND STATUS IS NULL",
            total=total,
            target_count=target,
            progress_count=pass_count,
            progress_base=total,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS"),
        )

    def _sql_conversion_summary(self) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        total = self._count(table)
        target = self._count(table, "STATUS_CONVERSION IS NULL")
        pass_count = self._count(table, "UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')")
        fail_count = self._count(
            table,
            "UPPER(TRIM(NVL(STATUS_CONVERSION, 'NULL'))) = 'FAIL' OR UPPER(TRIM(NVL(STATUS_CONVERSION, 'NULL'))) LIKE 'FAIL-%'",
        )
        return self._stage_summary(
            agent="SQL_CONVERSION",
            table=table,
            target_condition="STATUS_CONVERSION IS NULL",
            total=total,
            target_count=target,
            progress_count=pass_count,
            progress_base=total,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS_CONVERSION"),
        )

    def _sql_tuning_summary(self) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        missing = [col for col in ("STATUS_TUNING", "STATUS_CONVERSION") if col not in columns]
        if missing:
            return self._unavailable("SQL_TUNING", table, f"missing columns: {', '.join(missing)}")
        base_where = "UPPER(TRIM(STATUS_CONVERSION)) = 'PASS-CONVERSION'"
        total = self._count(table, base_where)
        target = self._count(table, f"{base_where} AND STATUS_TUNING IS NULL")
        pass_count = self._count(table, f"{base_where} AND UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')")
        fail_count = self._count(
            table,
            f"{base_where} AND (UPPER(TRIM(NVL(STATUS_TUNING, 'NULL'))) = 'FAIL' OR UPPER(TRIM(NVL(STATUS_TUNING, 'NULL'))) LIKE 'FAIL-%')",
        )
        return self._stage_summary(
            agent="SQL_TUNING",
            table=table,
            target_condition="STATUS_TUNING IS NULL AND STATUS_CONVERSION='PASS-CONVERSION'",
            total=total,
            target_count=target,
            progress_count=pass_count,
            progress_base=total - target,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS_TUNING", base_where),
        )

    def _sql_formatting_summary(self) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO")
        columns = self._available_columns("NEXT_SQL_INFO")
        missing = [col for col in ("STATUS_TUNING", "FORMATTED_SQL") if col not in columns]
        if missing:
            return self._unavailable("SQL_FORMATTING", table, f"missing columns: {', '.join(missing)}")
        base_where = "UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')"
        target_where = f"{base_where} AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)"
        applied_where = f"{base_where} AND FORMATTED_SQL IS NOT NULL AND DBMS_LOB.GETLENGTH(FORMATTED_SQL) > 0"
        total = self._count(table, base_where)
        target = self._count(table, target_where)
        applied = self._count(table, applied_where)
        return self._stage_summary(
            agent="SQL_FORMATTING",
            table=table,
            target_condition="STATUS_TUNING PASS and FORMATTED_SQL empty",
            total=total,
            target_count=target,
            progress_count=applied,
            progress_base=total,
            success_count=applied,
            success_base=total,
            pass_count=applied,
            fail_count=0,
            status_counts={"APPLIED": applied, "PENDING": target},
        )

    def _stage_summary(
        self,
        *,
        agent: str,
        table: str,
        target_condition: str,
        total: int,
        target_count: int,
        progress_count: int,
        progress_base: int,
        success_count: int,
        success_base: int,
        pass_count: int,
        fail_count: int,
        status_counts: dict[str, int],
    ) -> dict[str, Any]:
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
            "other_count": max(int(total or 0) - int(target_count or 0) - int(pass_count or 0) - int(fail_count or 0), 0),
            "progress": {"count": int(progress_count or 0), "base": int(progress_base or 0), "rate": self._pct(progress_count, progress_base)},
            "success": {"count": int(success_count or 0), "base": int(success_base or 0), "rate": self._pct(success_count, success_base)},
            "status_counts": status_counts,
        }

    def _build_answer(self, dashboard: dict[str, Any]) -> str:
        agents = dashboard.get("agents") or {}
        lines = ["# SmartMigrate Dashboard", "DB Migration 작업이 완료됐습니다. 현재 전체 작업 현황은 아래와 같습니다."]
        lines.extend(
            [
                "",
                "## 작업 현황",
                "| 순서 | 단계 | 작업 대상 | 잔여 | 성공 | 실패 | 기타 |",
                "|---:|---|---:|---:|---:|---:|---:|",
            ]
        )
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            priority = AGENT_ORDER.index((key, label)) + 1
            if not summary.get("available", True):
                lines.append(f"| {priority} | {label} | - | - | - | - | - |")
                continue
            lines.append(
                "| "
                f"{priority} | {label} | {self._num(summary.get('total'))} | "
                f"{self._num(summary.get('remaining_count', summary.get('target_count')))} | "
                f"{self._num(summary.get('pass_count'))} | {self._num(summary.get('fail_count'))} | "
                f"{self._num(summary.get('other_count'))} |"
            )
        lines.extend(["", "## Progress Graph", "![SmartMigrate Progress / Success](dashboard_chart)"])
        return "\n".join(lines)

    def _message(self, text: str, chart_url: str) -> Message:
        markdown_text = text.replace("(dashboard_chart)", f"({chart_url})")
        image_block = self._image_content_block(chart_url)
        try:
            return Message(text=markdown_text, files=[chart_url], content_blocks=[image_block])
        except TypeError:
            try:
                return Message(text=markdown_text, content_blocks=[image_block])
            except TypeError:
                return Message(text=markdown_text)

    def _chart_data_url(self, agents: dict[str, Any]) -> str:
        labels: list[str] = []
        progress_values: list[float] = []
        success_values: list[float] = []
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            if not summary.get("available", True):
                continue
            labels.append(label)
            progress = summary.get("progress") or {}
            success = summary.get("success") or {}
            progress_values.append(self._pct_value(progress.get("count", 0), progress.get("base", 0)))
            success_values.append(self._pct_value(success.get("count", 0), success.get("base", 0)))
        return self._bar_chart_png_data_url(labels, progress_values, success_values)

    def _image_content_block(self, chart_url: str) -> dict[str, Any]:
        if chart_url.startswith("data:image/png;base64,"):
            return {
                "type": "image",
                "base64": chart_url.split(",", 1)[1],
                "mime_type": "image/png",
                "caption": "SmartMigrate Progress / Success",
            }
        return {"type": "image", "urls": [chart_url], "caption": "SmartMigrate Progress / Success"}

    def _bar_chart_png_data_url(self, labels: list[str], progress_values: list[float], success_values: list[float]) -> str:
        import binascii
        import struct
        import zlib

        if not labels:
            labels = ["No Data"]
            progress_values = [0.0]
            success_values = [0.0]

        width, height = 760, 420
        pixels = bytearray([255, 255, 255] * width * height)

        def rect(x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int]) -> None:
            x1 = max(0, min(width, x1))
            x2 = max(0, min(width, x2))
            y1 = max(0, min(height, y1))
            y2 = max(0, min(height, y2))
            for y in range(y1, y2):
                row = y * width * 3
                for x in range(x1, x2):
                    offset = row + x * 3
                    pixels[offset : offset + 3] = bytes(color)

        left, top, chart_w, chart_h = 70, 52, 640, 260
        axis = (55, 65, 81)
        grid = (229, 231, 235)
        for tick in range(0, 101, 25):
            y = int(top + chart_h - (tick / 100 * chart_h))
            rect(left, y, left + chart_w, y + 1, grid)
        rect(left, top, left + 1, top + chart_h + 1, axis)
        rect(left, top + chart_h, left + chart_w + 1, top + chart_h + 1, axis)

        group_w = chart_w / max(len(labels), 1)
        bar_w = int(min(44, group_w * 0.28))
        for index in range(len(labels)):
            base_x = int(left + index * group_w + group_w / 2)
            for offset, value, color in (
                (-int(bar_w * 0.6), progress_values[index], (37, 99, 235)),
                (int(bar_w * 0.6), success_values[index], (22, 163, 74)),
            ):
                bar_h = int(max(0.0, min(float(value), 100.0)) / 100 * chart_h)
                x = base_x + offset - bar_w // 2
                y = top + chart_h - bar_h
                rect(x, y, x + bar_w, top + chart_h, color)

        raw = bytearray()
        for y in range(height):
            raw.append(0)
            start = y * width * 3
            raw.extend(pixels[start : start + width * 3])

        def chunk(name: bytes, data: bytes) -> bytes:
            crc = binascii.crc32(name + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)

        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )
        return f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"

    def _recommendation(self, agents: dict[str, dict[str, Any]]) -> dict[str, Any]:
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            count = int(summary.get("target_count") or 0)
            if summary.get("available", True) and count > 0:
                return {"agent": summary.get("agent"), "label": label, "target_count": count}
        return {}

    def _count(self, table: str, where_clause: str = "1=1") -> int:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where_clause}")
            row = cur.fetchone()
        return int(row[0] if row else 0)

    def _status_counts(self, table: str, status_column: str, where_clause: str = "1=1") -> dict[str, int]:
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

    def _available_columns(self, table_name: str) -> set[str]:
        table = self._clean_identifier(table_name)
        schema = str(self._db_config.get("system_schema") or "").strip().upper()
        with self._connect() as conn:
            cur = conn.cursor()
            if schema:
                cur.execute("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2", [schema, table])
            else:
                cur.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1", [table])
            rows = cur.fetchall()
        return {str(row[0]).upper() for row in rows}

    def _unavailable(self, agent: str, table: str, reason: str) -> dict[str, Any]:
        return {
            "agent": agent,
            "available": False,
            "table": table,
            "reason": reason,
            "total": 0,
            "target_count": 0,
            "pass_count": 0,
            "fail_count": 0,
            "other_count": 0,
            "progress": {"count": 0, "base": 0, "rate": "-"},
            "success": {"count": 0, "base": 0, "rate": "-"},
            "status_counts": {},
        }

    @contextmanager
    def _connect(self):
        import oracledb

        dsn = oracledb.makedsn(
            str(self._db_config.get("db_host") or "").strip(),
            int(self._db_config.get("db_port") or 1521),
            service_name=str(self._db_config.get("db_service_name") or "").strip(),
        )
        conn = oracledb.connect(
            user=str(self._db_config.get("db_username") or "").strip(),
            password=str(self._db_config.get("db_password") or ""),
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _has_db_config(self) -> bool:
        return all(str(self._db_config.get(name) or "").strip() for name in ("db_host", "db_service_name", "db_username"))

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(self._db_config.get("system_schema") or "").strip().upper()
        return f"{self._clean_identifier(schema)}.{table}" if schema else table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _pct(self, numerator: int, denominator: int) -> str:
        denominator = int(denominator or 0)
        if denominator <= 0:
            return "-"
        return f"{(int(numerator or 0) / denominator) * 100:.1f}%"

    def _pct_value(self, numerator: Any, denominator: Any) -> float:
        base = self._num(denominator)
        if base <= 0:
            return 0.0
        return max(0.0, min(self._num(numerator) / base * 100, 100.0))

    def _num(self, value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

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
            raise ValueError("loop_done must be a JSON object")
        return parsed
