from __future__ import annotations

import logging

from datetime import datetime
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message


# =============================================================================
# 00A Log Runtime Start
# =============================================================================
# Langflow 대화형 workflow의 가장 앞단에서 실행되는 로깅 초기화 컴포넌트다.
#
# 핵심 책임:
# 1. 사용자의 원본 채팅 입력을 뒤쪽 컴포넌트로 그대로 전달한다.
# 2. smartmigrate.workflow logger에 Oracle DB handler를 등록한다.
# 3. 이후 10C/12C/15C/17C 등에서 남기는 workflow_log extra payload를
#    NEXT_MIG_LOG 한 곳에 누적 저장할 수 있게 한다.
#
# schema 처리 원칙:
# - Langflow 입력 system_schema가 있으면 해당 schema의 로그 테이블/시퀀스를 사용한다.
# - system_schema가 비어 있으면 schema prefix를 붙이지 않고 현재 접속 schema의
#   NEXT_MIG_LOG와 MIGRATION_LOG_SEQ를 사용한다.
# - 이 파일에서는 특정 schema를 기본값으로 가정하지 않는다.
#
# 설계상 중요한 점:
# - 이 컴포넌트는 업무 판단이나 라우팅을 하지 않는다.
# - 로깅 핸들러를 매 요청마다 새로 등록하므로, 이전 요청에서 남은 handler가
#   중복 insert를 만들지 않도록 기존 handler를 모두 제거한다.
# - 로그 insert 실패는 전체 workflow 실패로 전파하지 않고 status에만 기록한다.
#   업무 처리 자체보다 로그 DB insert가 약한 의존성이기 때문이다.
# =============================================================================
LOGGER_NAME = "smartmigrate.workflow"
HANDLER_MARKER = "SmartMigrateHandler"


def create_db_connection(db_config: dict[str, Any]):
    # Oracle 연결은 모든 로그 insert에서 재사용된다.
    # Langflow 입력값을 이미 _db_config()에서 문자열/정수로 정리하므로
    # 여기서는 DSN 생성과 연결 생성만 담당한다.
    import oracledb

    dsn = oracledb.makedsn(
        db_config["host"],
        int(db_config.get("port") or 1521),
        service_name=db_config["service_name"],
    )
    return oracledb.connect(user=db_config["username"], password=db_config["password"], dsn=dsn)


class SmartMigrateDBHandler(logging.Handler):
    # Python logging.Handler를 Oracle insert handler로 확장한 클래스.
    #
    # 일반 logger 호출:
    #   logger.info("message", extra={"workflow_log": [...]})
    #
    # 위 형태의 workflow_log를 표준화해서 NEXT_MIG_LOG row로 저장한다.
    # handler 내부에 records를 보관하는 이유는 Langflow 실행 중 디버깅할 때
    # 실제 insert 시도 payload를 component status에서 확인하기 위함이다.

    def __init__(self, db_config: dict[str, Any]):
        super().__init__(level=logging.DEBUG)
        self.handler_marker = HANDLER_MARKER
        self.db_config = dict(db_config)
        self.connection = create_db_connection(db_config)
        self.records: list[dict[str, Any]] = []
        self.insert_error = None

    def emit(self, record: logging.LogRecord) -> None:
        # logging 모듈이 각 record를 전달할 때마다 호출된다.
        # _event()에서 list/dict/일반 log record를 하나의 dict 형태로 정규화한 뒤
        # NEXT_MIG_LOG 컬럼 길이에 맞춰 문자열을 잘라 저장한다.
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

    def close(self) -> None:
        # Langflow 그래프가 여러 번 실행될 수 있으므로 handler 종료 시
        # Oracle connection도 명시적으로 닫는다.
        try:
            connection = getattr(self, "connection", None)
            if connection is not None:
                connection.close()
                self.connection = None
        finally:
            super().close()

    def _event(self, record: logging.LogRecord) -> dict[str, Any]:
        # workflow_log extra payload 호환 처리.
        #
        # 현재 권장 포맷:
        #   [map_id, mig_kind, log_type, log_level, step_name, status,
        #    retry_count, generate_sql]
        #
        # 일부 과거 컴포넌트는 message와 retry_count 위치가 달랐기 때문에
        # len(event)와 int-like 여부를 보고 구버전 포맷도 받아준다.
        event = getattr(record, "workflow_log", None)
        if isinstance(event, (list, tuple)):
            message = record.getMessage() or "noMessage"
            retry_count = event[6] if len(event) > 6 else 0
            generate_sql = event[7] if len(event) > 7 else None
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

    def _insert_row(self, row: dict[str, Any]) -> None:
        # NEXT_MIG_LOG에 실제 insert를 수행한다.
        #
        # 주의:
        # - LOG_ID는 MIGRATION_LOG_SEQ.NEXTVAL을 사용한다.
        # - GENERATE_SQL은 CLOB일 수 있으므로 row dict 그대로 바인딩한다.
        # - insert 실패는 raise하지 않는다. 로그 저장 실패가 업무 workflow를
        #   멈추면 장애 원인을 더 보기 어려워지기 때문이다.
        cursor = None
        try:
            cursor = self.connection.cursor()
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
            self.insert_error = str(exc)
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    def _qualify(self, object_name: str) -> str:
        # system_schema가 입력된 경우에만 schema.object 형식으로 qualify한다.
        # 비어 있으면 Oracle 현재 접속 schema의 객체를 그대로 사용한다.
        schema = str(self.db_config.get("system_schema") or "").strip().upper()
        return f"{schema}.{object_name}" if schema else object_name

    def _is_int_like(self, value: Any) -> bool:
        try:
            int(value)
            return True
        except Exception:
            return False

    def _to_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default


class NewType00ALogRuntimeStart(Component):
    # Langflow custom component 진입점.
    # 이 컴포넌트의 output Message는 사용자의 입력 텍스트 그대로이며,
    # 뒤쪽 01 classifier가 이 값을 받아 의도 분류를 수행한다.
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
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]

    outputs = [Output(display_name="Message", name="message", method="run", types=["Message"])]

    def run(self) -> Message:
        # 매 요청마다 logger handler를 새로 구성한다.
        # 같은 Python 프로세스에서 Langflow 컴포넌트가 재사용되면 handler가 누적될 수
        # 있으므로, 기존 handler 제거가 없으면 동일 로그가 여러 번 DB에 insert된다.
        text = str(getattr(self, "input_text", "") or "")
        logger = logging.getLogger(LOGGER_NAME)
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

    def _db_config(self) -> dict[str, Any]:
        return {
            "host": str(getattr(self, "db_host", "") or "").strip(),
            "port": int(getattr(self, "db_port", 1521) or 1521),
            "service_name": str(getattr(self, "db_service_name", "") or "").strip(),
            "username": str(getattr(self, "db_username", "") or "").strip(),
            "password": self._secret_to_str(getattr(self, "db_password", "")),
            "system_schema": str(getattr(self, "system_schema", "") or "").strip(),
        }

    def _secret_to_str(self, value: Any) -> str:
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value() or "")
        return str(value or "")
