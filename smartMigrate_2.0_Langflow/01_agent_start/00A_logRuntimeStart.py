from __future__ import annotations

import logging

from datetime import datetime
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message


# =============================================================================
# =============================================================================
# Langflow 대화형 workflow의 가장 앞에서 DB logging handler를 새로 등록하고,
# 사용자의 원본 입력을 다음 컴포넌트로 그대로 전달한다.
#
# 이 컴포넌트는 업무 판단이나 라우팅을 하지 않는다. workflow 전역에서 같은 logger를
# 사용하도록 Oracle logging handler만 준비하는 시작점이다.
# =============================================================================
LOGGER_NAME = "smartmigrate.workflow"
HANDLER_MARKER = "SmartMigrateHandler"


# Oracle DSN을 만들고 workflow 로그 적재용 DB connection을 생성한다.
def create_db_connection(db_config: dict[str, Any]):
    import oracledb

    dsn = oracledb.makedsn(
        db_config["host"],
        int(db_config.get("port") or 1521),
        service_name=db_config["service_name"],
    )
    return oracledb.connect(user=db_config["username"], password=db_config["password"], dsn=dsn)


class SmartMigrateDBHandler(logging.Handler):
    # Python logging.Handler를 Oracle insert handler로 확장한 클래스다.
    # logger.info(..., extra={"workflow_log": [...]}) 형태의 payload를 NEXT_MIG_LOG에 적재한다.

    # 컴포넌트나 helper 객체의 초기 상태와 설정 값을 준비한다.
    def __init__(self, db_config: dict[str, Any]):
        super().__init__(level=logging.DEBUG)
        self.handler_marker = HANDLER_MARKER
        self.db_config = dict(db_config)
        self.connection = create_db_connection(db_config)
        self.records: list[dict[str, Any]] = []
        self.insert_error = None

    # logging record를 NEXT_MIG_LOG에 저장할 row로 변환해 insert한다.
    def emit(self, record: logging.LogRecord) -> None:
        # logging 모듈이 전달한 record를 DB row 구조로 변환한다.
        # records에도 남겨 Langflow status에서 마지막 적재 시도 내용을 확인할 수 있게 한다.
        event = self._event(record)
        row = {
            "created_at": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f"),
            "map_id": str(event.get("map_id") or 0)[:100],
            "mig_kind": str(event.get("mig_kind") or "WORKFLOW")[:100],
            "log_type": str(event.get("log_type") or "")[:20],
            "log_level": str(event.get("log_level") or "noLevelName")[:20],
            "step_name": str(event.get("step_name") or "")[:50],
            "status": str(event.get("status") or "noStatus")[:20],
            "message": str(event.get("message") or "noMessage")[:4000],
            "retry_count": self._to_int(event.get("retry_count"), 0),
            "generate_sql": event.get("generate_sql"),
        }
        self.records.append(row)
        self._insert_row(row)

    # handler가 잡고 있는 DB connection을 닫아 Langflow 반복 실행 시 누수를 막는다.
    def close(self) -> None:
        # Langflow graph가 같은 Python process에서 반복 실행될 수 있으므로,
        # handler 교체 시 Oracle connection을 명시적으로 닫는다.
        try:
            connection = getattr(self, "connection", None)
            if connection is not None:
                connection.close()
                self.connection = None
        finally:
            super().close()

    # logging record의 workflow_log payload를 NEXT_MIG_LOG 컬럼 구조로 변환한다.
    def _event(self, record: logging.LogRecord) -> dict[str, Any]:
        event = getattr(record, "workflow_log", None)
        if isinstance(event, (list, tuple)):
            message = record.getMessage() or "noMessage"
            retry_count = event[6] if len(event) > 6 else 0
            generate_sql = event[7] if len(event) > 7 else None

            # 일부 이전 컴포넌트는 message와 retry_count 위치가 서로 다른 payload를 남겼다.
            # 기존 로그 호출을 깨지 않도록 int 여부를 기준으로 legacy payload도 받아준다.
            if len(event) > 7 and not self._is_int_like(event[6]) and self._is_int_like(event[7]):
                message = event[6]
                retry_count = event[7]
                generate_sql = event[8] if len(event) > 8 else None
            return {
                "map_id": event[0] if len(event) > 0 else 0,
                "mig_kind": event[1] if len(event) > 1 else "WORKFLOW",
                "log_type": event[2] if len(event) > 2 else "PY_LOG",
                "log_level": event[3] if len(event) > 3 else "noLevelName",
                "step_name": event[4] if len(event) > 4 else "LOGGING",
                "status": event[5] if len(event) > 5 else "noStatus",
                "message": message,
                "retry_count": retry_count,
                "generate_sql": generate_sql,
            }
        if isinstance(event, dict):
            event = dict(event)
            event["message"] = event.get("message") or record.getMessage() or "noMessage"
            event["generate_sql"] = event.get("generate_sql")
            return event
        return {
            "map_id": 0,
            "mig_kind": "WORKFLOW",
            "log_type": "PY_LOG",
            "log_level": "noLevelName",
            "step_name": "LOGGING",
            "status": "noStatus",
            "message": record.getMessage() or "noMessage",
            "retry_count": 0,
            "generate_sql": None,
        }

    # 변환된 로그 row를 NEXT_MIG_LOG에 insert하고 commit한다.
    def _insert_row(self, row: dict[str, Any]) -> None:
        cursor = None
        try:
            cursor = self.connection.cursor()

            # system_schema가 입력되어 있으면 schema prefix를 붙이고,
            # 비어 있으면 현재 접속 schema의 NEXT_MIG_LOG와 MIGRATION_LOG_SEQ를 그대로 사용한다.
            cursor.execute(
                f"""
                INSERT INTO {self._qualify("NEXT_MIG_LOG")} (
                    LOG_ID, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, RETRY_COUNT, GENERATE_SQL, CREATED_AT
                ) VALUES (
                    {self._qualify("MIGRATION_LOG_SEQ")}.NEXTVAL,
                    :map_id,
                    :mig_kind,
                    :log_type,
                    :log_level,
                    :step_name,
                    :status,
                    :message,
                    :retry_count,
                    :generate_sql,
                    TO_TIMESTAMP(:created_at, 'YYYY-MM-DD HH24:MI:SS.FF6')
                )
                """,
                row,
            )
            self.connection.commit()
            self.insert_error = None
        except Exception as exc:
            # 로그 적재 실패가 본 업무 workflow 실패로 전파되지 않게 status에만 남긴다.
            self.insert_error = str(exc)
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    # system_schema가 명시된 테이블명을 schema-qualified 이름으로 만든다.
    def _qualify(self, object_name: str) -> str:
        schema = str(self.db_config.get("system_schema") or "").strip().upper()
        if not schema:
            raise ValueError("System Schema를 입력해야 합니다.")
        return f"{schema}.{object_name}"

    # 상태나 값이 특정 조건에 해당하는지 boolean으로 판단한다.
    def _is_int_like(self, value: Any) -> bool:
        try:
            int(value)
            return True
        except Exception:
            return False

    # 문자/숫자/NULL 값을 정수로 변환하고 실패하면 안전한 기본값을 반환한다.
    def _to_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default


class NewType00ALogRuntimeStart(Component):
    display_name = "00A Log Runtime Start"
    description = "Register SmartMigrate workflow DB logging handler and pass the chat input through."
    name = "NewType00ALogRuntimeStart"

    inputs = [
        MessageTextInput(name="input_text", display_name="Input Text", required=False),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
    ]
    outputs = [Output(display_name="Message", name="message", method="run", types=["Message"])]

    # Langflow output 진입점에서 입력을 검증하고 이 컴포넌트의 주요 실행 흐름을 시작한다.
    def run(self) -> Message:
        text = str(getattr(self, "input_text", "") or "")
        logger = logging.getLogger(LOGGER_NAME)

        # 같은 Langflow process에서 이전 요청의 handler가 남아 있으면 로그가 중복 insert될 수 있다.
        # 새 요청을 시작할 때 기존 handler를 닫고 이번 요청용 handler만 다시 등록한다.
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        handler = SmartMigrateDBHandler(self._db_config())
        logger.addHandler(handler)
        logger.info(
            f"CHAT INPUT, MESSAGE : {text}",
            extra={"workflow_log": [0, "WORKFLOW", "CHAT_INPUT", "INFO", "MESSAGE", "START", 0]},
        )
        self.status = {"ok": handler.insert_error is None, "db_insert_error": handler.insert_error}
        return Message(text=text)

    # payload와 Langflow 입력에서 Oracle 접속 및 schema 설정을 모은다.
    def _db_config(self) -> dict[str, Any]:
        return {
            "host": str(getattr(self, "db_host", "") or "").strip(),
            "port": int(getattr(self, "db_port", 1521) or 1521),
            "service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "username": str(getattr(self, "db_username", "") or "").strip(),
            "password": self._secret_to_str(getattr(self, "db_password", "")),
            "system_schema": str(getattr(self, "system_schema", "") or "").strip(),
        }

    # Langflow Secret 입력을 일반 문자열로 꺼내 client library 설정에 사용한다.
    def _secret_to_str(self, value: Any) -> str:
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value() or "")
        return str(value or "")
