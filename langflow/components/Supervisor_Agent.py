from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, SecretStrInput, StrInput, Output
from lfx.schema.data import Data

_ROOT_DIR = Path(__file__).resolve().parents[2]
_RUNTIME_DIR = _ROOT_DIR / "runtime"
_LOG_FILE = _RUNTIME_DIR / "agent.log"
_COMMAND_FILE = _RUNTIME_DIR / "chat_command.json"
_PID_FILE = _RUNTIME_DIR / "supervisor_agent.pid"

DEFAULT_DB_CONFIG = {
    "db_host": "10.0.0.1",
    "db_port": 1521,
    "db_service_name": "ORCL",
    "db_username": "SMARTMIGRATE",
    "db_password": "password",
    "system_schema": "SFAADM",
    "source_schema": "SFAMIG",
    "target_schema": "SFAADM",
}

DEFAULT_LLM_CONFIG = {
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "claude-haiku-4-5-20251001",
    "llm_max_tokens": 4096,
    "llm_timeout_seconds": 900,
}

AUTO_INSTALL_MISSING_PACKAGES = True

SUPERVISOR_SYSTEM_PROMPT = """
You are the SmartMigrate background Supervisor Agent.

No chat input is provided. The runtime polls jobs every cycle and sends you the
current pending job snapshot.

Choose exactly one route for the current cycle:
- run_data_migration: use when migration_job exists.
- run_sql_conversion: use only when migration_job is null and sql_job exists.
- no_job: use only when both migration_job and sql_job are null.

Rules:
- DB_MIGRATION always has priority over SQL_CONVERSION.
- If a user request is provided, reflect it for this cycle only.
- Run at most one job per cycle.
- Never request user input.
- Do not invent a job that was not provided in the snapshot.
- Return JSON only. Do not include markdown.

Required JSON schema:
{"route":"run_data_migration | run_sql_conversion | no_job","reason":"short reason"}
""".strip()


def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("migration_agent")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        try:
            import io

            sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding="utf-8", line_buffering=True)
        except Exception:
            pass
        formatter = logging.Formatter("%(asctime)s - [%(name)s] [%(levelname)s] - %(message)s")
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.DEBUG)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        try:
            _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:
            pass
        logger.propagate = False
    return logger


logger = _setup_logger()


class BatchAgentCommandTool(Component):
    display_name = "Batch Supervisor Agent"
    description = "Runs the always-on SmartMigration supervisor loop without chat input."
    name = "BatchSupervisorAgent"
    icon = "Bot"

    _db_cache: dict[str, Any] = {}

    _state: dict[str, Any] = {
        "running": False,
        "run_id": None,
        "loop_no": 0,
        "started_at": None,
        "updated_at": None,
        "last_event": None,
        "last_agent": None,
        "last_job_id": None,
        "last_job_status": None,
        "last_error": None,
    }

    inputs = [
        MessageTextInput(
            name="run_yn",
            display_name="Run YN",
            required=False,
            info="Set Y to start the background supervisor loop. Set N to request stop.",
        ),
        MessageTextInput(
            name="chat_input",
            display_name="Chat Input",
            required=False,
            info="Optional user command. Example: run migration map_id=101 or run sql_id=SEL_001 space_nm=userMapper.",
        ),
        MessageTextInput(name="mig_sql_prompt", display_name="MIG SQL Prompt", required=False),
        MessageTextInput(name="verify_sql_prompt", display_name="VERIFY SQL Prompt", required=False),
        MessageTextInput(name="to_sql_prompt", display_name="TO SQL Prompt", required=False),
        MessageTextInput(name="bind_sql_prompt", display_name="BIND SQL Prompt", required=False),
        MessageTextInput(name="test_sql_prompt", display_name="TEST SQL Prompt", required=False),
    ]

    outputs = [
        Output(display_name="Result", name="result", method="run_supervisor"),
    ]

    def run_supervisor(self) -> Data:
        try:
            config = self._snapshot_config()

            if self._run_yn_equals_y(config):
                result = self._start_background(config)
            else:
                result = {
                    "ok": False,
                    "status": "ignored",
                    "running": bool(self.__class__._state.get("running")),
                    "requested_running": False,
                    "message": "Run YN input must be Y to start. Stop is requested by Chat Agent through NEXT_BATCH_CONTROL.",
                }

            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _start_background(self, config: dict[str, Any]) -> dict[str, Any]:
        """Start the blocking supervisor loop in a separate Python process.

        This mirrors the original Streamlit agent_control.py behavior. Langflow
        receives a quick response, while the child process owns Supervisor_loop().
        """
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        chat_command = str(config.get("chat_input") or "").strip()

        if self._is_background_supervisor_running(config):
            command_queued = False
            if chat_command:
                self._write_chat_command_file(chat_command)
                config["chat_input"] = ""
                command_queued = True
            control = self._read_batch_control(config)
            return {
                "ok": True,
                "status": "already_running",
                "running": True,
                "requested_running": True,
                "run_id": control.get("run_id"),
                "pid": self._read_pid(),
                "mode": "background_process",
                "command_queued": command_queued,
                "message": (
                    "Batch supervisor is already running. "
                    "Chat input was queued for the next cycle." if command_queued
                    else "Batch supervisor is already running."
                ),
            }

        run_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        config_file = _RUNTIME_DIR / f"supervisor_config_{run_id}.json"
        config_file.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["SMARTMIGRATE_MONITOR_CONFIG"] = str(config_file)
        env["SMARTMIGRATE_RUN_YN"] = "Y"

        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=str(_ROOT_DIR),
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        _PID_FILE.write_text(str(process.pid), encoding="utf-8")
        return {
            "ok": True,
            "status": "started",
            "running": True,
            "requested_running": True,
            "pid": process.pid,
            "mode": "background_process",
            "config_file": str(config_file),
            "message": "Batch supervisor process started.",
        }

    def _start(self, config: dict[str, Any]) -> dict[str, Any]:
        run_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        if self.__class__._state.get("running"):
            state = dict(self.__class__._state)
            self._write_batch_log_safe(
                config,
                state.get("run_id") or run_id,
                int(state.get("loop_no") or 0),
                "ALREADY_RUNNING",
                message="Batch supervisor is already running.",
            )
            return {
                "ok": True,
                "status": "already_running",
                "running": True,
                "requested_running": True,
                "run_id": state.get("run_id") or run_id,
                "mode": "blocking_loop",
                "message": "Batch supervisor is already running.",
            }

        acquired = self._try_acquire_batch_control(config, run_id)
        if not acquired:
            control = self._read_batch_control(config)
            self._write_batch_log_safe(
                config,
                control.get("run_id") or run_id,
                int(control.get("loop_no") or 0),
                "ALREADY_RUNNING",
                message="Batch supervisor is already running in NEXT_BATCH_CONTROL.",
            )
            return {
                "ok": True,
                "status": "already_running",
                "running": True,
                "requested_running": True,
                "run_id": control.get("run_id") or run_id,
                "mode": "blocking_loop",
                "message": "Batch supervisor is already running in NEXT_BATCH_CONTROL.",
            }

        _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        self._write_batch_log_safe(config, run_id, 0, "START", message="Batch agent started.")
        self.Supervisor_loop(config, run_id)
        state = dict(self.__class__._state)
        return {
            "ok": True,
            "status": state.get("last_event") or "stopped",
            "running": False,
            "requested_running": self._run_yn_equals_y(config),
            "run_id": run_id,
            "mode": "blocking_loop",
            "message": "Batch Supervisor Agent loop finished.",
        }

    def _stop(self, config: dict[str, Any]) -> dict[str, Any]:
        # Stop is normally requested by Chat Agent through NEXT_BATCH_CONTROL.
        # This method remains as a local helper for compatibility, but RunYN=N
        # no longer calls it.
        state = self._status(config)
        run_id = state.get("run_id") or datetime.now().strftime("%Y%m%d%H%M%S%f")
        loop_no = int(state.get("loop_no") or 0)
        self._request_batch_stop(config)
        self._write_batch_log_safe(config, run_id, loop_no, "STOP_REQUESTED", message="Stop requested.")
        self.__class__._state["running"] = False
        self.__class__._state["last_event"] = "STOP_REQUESTED"
        self.__class__._state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return {
            "ok": True,
            "status": "stop_requested",
            "running": False,
            "requested_running": False,
            "run_id": run_id,
            "message": "Run YN is N. The blocking loop will not start.",
        }

    def _status(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        state = dict(self.__class__._state)
        state["running"] = bool(self.__class__._state.get("running"))
        state["requested_running"] = self._run_yn_equals_y(config) if config else None
        state["status_source"] = "memory"
        if config:
            control = self._read_batch_control(config)
            state["control"] = control
            state["stop_requested"] = str(control.get("stop_requested_yn") or "").upper() == "Y"
            state["status_source"] = "memory+NEXT_BATCH_CONTROL"
            state["background_pid"] = self._read_pid()
            state["background_running"] = self._is_background_supervisor_running(config)
        else:
            state["stop_requested"] = not bool(state["requested_running"])
        return {"ok": True, **state}

    def _read_pid(self) -> int | None:
        try:
            return int(_PID_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    def _pid_alive(self, pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False

    def _is_batch_control_running(self, config: dict[str, Any]) -> bool:
        table = self._batch_control_table(config)
        rows = self._query(
            config,
            f"""
            SELECT COUNT(*)
              FROM {table}
             WHERE CONTROL_NAME = 'BATCH_AGENT'
               AND UPPER(TRIM(STATUS)) = 'RUNNING'
               AND UPPER(TRIM(NVL(STOP_REQUESTED_YN, 'N'))) = 'N'
               AND HEARTBEAT_AT IS NOT NULL
               AND HEARTBEAT_AT >= CURRENT_TIMESTAMP - NUMTODSINTERVAL(:1, 'SECOND')
            """,
            [300],
        )
        return bool(rows and int(rows[0][0] or 0) > 0)

    def _is_background_supervisor_running(self, config: dict[str, Any]) -> bool:
        try:
            if self._is_batch_control_running(config):
                return True
        except Exception:
            logger.exception("[BatchSupervisor] failed to inspect NEXT_BATCH_CONTROL running state")

        pid = self._read_pid()
        running = self._pid_alive(pid)
        if not running and _PID_FILE.exists():
            _PID_FILE.unlink(missing_ok=True)
        return running

    def _write_chat_command_file(self, command: str) -> None:
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        _COMMAND_FILE.write_text(
            json.dumps({"command": str(command or "").strip()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _console(message: str) -> None:
        logger.info(f"[BatchSupervisor] {message}")

    def _raise_if_batch_stop_requested(self) -> None:
        config = getattr(self, "_batch_runtime_config", None)
        run_id = str((config or {}).get("batch_run_id") or "")
        if not config:
            if not self._run_yn_equals_y(config):
                raise InterruptedError("Batch stop requested.")
            return
        should_continue, reason = self._batch_should_continue(config, run_id)
        if not should_continue:
            raise InterruptedError(reason)

    def _run_yn_value(self, config: dict[str, Any] | None = None) -> str:
        value = getattr(self, "run_yn", None)
        if value is None and config:
            value = config.get("run_yn")
        return str(value if value is not None else "").strip().upper()

    def _run_yn_equals_y(self, config: dict[str, Any] | None = None) -> bool:
        return self._run_yn_value(config) == "Y"

    def _run_yn_equals_n(self, config: dict[str, Any] | None = None) -> bool:
        return self._run_yn_value(config) == "N"

    def Supervisor_loop(self, config: dict[str, Any], run_id: str) -> None:
        cls = self.__class__
        config = {**config, "batch_run_id": run_id}
        self._batch_runtime_config = config
        self._console(f"Supervisor_loop entered run_id={run_id} run_yn={self._run_yn_value(config)}")
        cls._state.update(
            {
                "running": True,
                "run_id": run_id,
                "loop_no": 0,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "last_event": "START",
                "last_agent": None,
                "last_job_id": None,
                "last_job_status": None,
                "last_error": None,
            }
        )
        try:
            while self._batch_should_continue(config, run_id)[0]:
                cls._state["loop_no"] = int(cls._state.get("loop_no") or 0) + 1
                loop_no = int(cls._state["loop_no"])
                cls._state["updated_at"] = datetime.now().isoformat(timespec="seconds")
                cls._state["last_event"] = "LOOP_START"
                self._update_batch_control_heartbeat(config, run_id, loop_no, "LOOP_START")

                started = time.perf_counter()
                self._console(f"cycle {loop_no} started")

                try:
                    result = self._run_batch_supervisor_cycle(config)
                    elapsed = round(time.perf_counter() - started, 3)
                    event_type = "JOB_SUCCESS" if result.get("job_executed") else "NO_JOB"
                    if str(result.get("status") or "").strip().upper() == "STOPPED":
                        event_type = "JOB_STOPPED"
                    elif result.get("job_executed") and not result.get("ok"):
                        event_type = "JOB_FAIL"
                    sleep_seconds = 0 if result.get("job_executed") else int(config["no_job_sleep_seconds"])
                    message = str(result.get("message") or "")
                    error_message = result.get("error")
                    agent_name = result.get("agent")
                    job_id = result.get("job_id")
                    job_status = result.get("status")

                    cls._state["last_event"] = event_type
                    cls._state["last_agent"] = agent_name
                    cls._state["last_job_id"] = job_id
                    cls._state["last_job_status"] = job_status
                    cls._state["last_error"] = error_message
                    cls._state["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    self._update_batch_control_heartbeat(
                        config,
                        run_id,
                        loop_no,
                        event_type,
                        agent_name=agent_name,
                        job_id=job_id,
                        job_status=job_status,
                        error_message=error_message,
                        message=message,
                    )

                    self._write_batch_log_safe(
                        config,
                        run_id,
                        loop_no,
                        event_type,
                        agent_name=agent_name,
                        job_id=job_id,
                        job_status=job_status,
                        message=message,
                        error_message=error_message,
                        sleep_seconds=sleep_seconds,
                        elapsed_seconds=elapsed,
                    )
                    self._console(
                        f"cycle {loop_no} {event_type}: "
                        f"agent={agent_name or '-'} job_id={job_id or '-'} "
                        f"status={job_status or '-'} message={message or '-'}"
                    )
                    if event_type == "JOB_STOPPED":
                        break
                    if sleep_seconds > 0:
                        self._interruptible_sleep(sleep_seconds, config, run_id)

                except InterruptedError as exc:
                    elapsed = round(time.perf_counter() - started, 3)
                    cls._state["last_event"] = "JOB_STOPPED"
                    cls._state["last_error"] = str(exc)
                    cls._state["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    self._write_batch_log_safe(
                        config,
                        run_id,
                        loop_no,
                        "JOB_STOPPED",
                        message=str(exc),
                        elapsed_seconds=elapsed,
                    )
                    self._console(f"cycle {loop_no} JOB_STOPPED: {exc}")
                    break

                except ConnectionError as exc:
                    elapsed = round(time.perf_counter() - started, 3)
                    cls._state["running"] = False
                    cls._state["last_event"] = "FATAL_ERROR"
                    cls._state["last_error"] = str(exc)
                    cls._state["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    self._write_batch_log_safe(
                        config,
                        run_id,
                        loop_no,
                        "FATAL_ERROR",
                        message="Batch worker stopped because DB access failed.",
                        error_message=str(exc),
                        elapsed_seconds=elapsed,
                    )
                    self._console(f"cycle {loop_no} FATAL_ERROR: {exc}")
                    break

                except Exception as exc:
                    elapsed = round(time.perf_counter() - started, 3)
                    error_message = f"{exc}\n{traceback.format_exc()}"
                    cls._state["last_event"] = "LOOP_ERROR"
                    cls._state["last_error"] = str(exc)
                    cls._state["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    self._write_batch_log_safe(
                        config,
                        run_id,
                        loop_no,
                        "LOOP_ERROR",
                        message="Unexpected batch loop error.",
                        error_message=error_message,
                        sleep_seconds=int(config["error_sleep_seconds"]),
                        elapsed_seconds=elapsed,
                    )
                    self._console(f"cycle {loop_no} LOOP_ERROR: {exc}")
                    self._interruptible_sleep(int(config["error_sleep_seconds"]), config, run_id)
        finally:
            cls._state["running"] = False
            cls._state["last_event"] = "STOPPED"
            cls._state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._mark_batch_control_stopped(config, run_id)
            if self._read_pid() == os.getpid():
                _PID_FILE.unlink(missing_ok=True)
            self._write_batch_log_safe(config, run_id, int(cls._state.get("loop_no") or 0), "STOPPED", message="Batch agent stopped.")

    def _run_batch_supervisor_cycle(self, config: dict[str, Any]) -> dict[str, Any]:
        """Run one LangGraph supervisor cycle with an LLM supervisor decision.

        The graph owns the orchestration shape:
        poll_jobs -> supervisor_decide -> run_data_migration | run_sql_conversion | no_job.
        The supervisor prompt decides the route, while the route function applies
        a minimal existence guard so an impossible route cannot execute.
        """
        self._ensure_runtime_dependencies(config)

        from typing import TypedDict

        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI
        from langgraph.graph import END, START, StateGraph

        class BatchSupervisorState(TypedDict, total=False):
            migration_job: dict[str, Any] | None
            sql_job: dict[str, Any] | None
            chat_command: str
            chat_target: dict[str, Any]
            decision: dict[str, Any]
            result: dict[str, Any]

        self._batch_runtime_config = config
        self._raise_if_batch_stop_requested()
        chat_command = self._consume_chat_command(config)
        chat_target = self._parse_chat_target(chat_command)
        if chat_command:
            logger.info(f"[Supervisor] 채팅 명령 수신: {chat_command}")

        def poll_jobs(_: BatchSupervisorState) -> BatchSupervisorState:
            self._raise_if_batch_stop_requested()
            migration_job = None
            sql_job = None
            if chat_target.get("map_id") is not None:
                migration_job = self._poll_next_migration_job(config, map_id=int(chat_target["map_id"]))
            elif chat_target.get("sql_id"):
                sql_job = self._poll_next_sql_conversion_job(
                    config,
                    sql_id=str(chat_target["sql_id"]),
                    space_nm=str(chat_target.get("space_nm") or "") or None,
                )
            else:
                migration_job = self._poll_next_migration_job(config)
                sql_job = None if migration_job else self._poll_next_sql_conversion_job(config)
            self._console(
                "poll result: "
                f"chat_target={chat_target or '-'} "
                f"migration_job={migration_job or '-'} "
                f"sql_job={sql_job or '-'}"
            )
            return {
                "migration_job": migration_job,
                "sql_job": sql_job,
                "chat_command": chat_command,
                "chat_target": chat_target,
            }

        def supervisor_decide(state: BatchSupervisorState) -> BatchSupervisorState:
            self._raise_if_batch_stop_requested()
            payload = {
                "chat_command": state.get("chat_command") or "",
                "chat_target": state.get("chat_target") or {},
                "migration_job": state.get("migration_job"),
                "sql_job": state.get("sql_job"),
                "policy": [
                    "If chat_command is present, reflect it in this cycle.",
                    "When chat_target contains map_id, run only that migration job if it is runnable.",
                    "When chat_target contains sql_id, run only that SQL conversion job if it is runnable.",
                    "Choose exactly one route for this supervisor cycle.",
                    "Available routes: run_data_migration, run_sql_conversion, no_job.",
                    "Run at most one job per cycle.",
                    "DB_MIGRATION has priority when a migration_job exists.",
                    "Choose run_sql_conversion only when migration_job is null and sql_job exists.",
                    "Choose no_job only when both migration_job and sql_job are null.",
                ],
                "required_json_schema": {
                    "route": "run_data_migration | run_sql_conversion | no_job",
                    "reason": "short reason",
                },
            }
            messages = [
                SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
            ]
            llm_kwargs: dict[str, Any] = {
                "model": config["llm_model"],
                "api_key": config["llm_api_key"],
                "max_tokens": min(int(config["llm_max_tokens"] or 4096), 1024),
                "timeout": config["llm_timeout_seconds"],
                "temperature": 0,
            }
            if config.get("llm_base_url"):
                llm_kwargs["base_url"] = config["llm_base_url"]
            response = ChatOpenAI(**llm_kwargs).invoke(messages)
            raw_decision = str(getattr(response, "content", "") or "").strip()
            decision = self._parse_supervisor_decision(raw_decision)
            self._console(
                "[SupervisorDecision] "
                f"route={decision.get('route') or '-'} "
                f"reason={decision.get('reason') or '-'} "
                f"raw={raw_decision[:500]}"
            )
            return {"decision": decision}

        def route_after_decision(state: BatchSupervisorState) -> str:
            route = str((state.get("decision") or {}).get("route") or "").strip()
            migration_job = state.get("migration_job")
            sql_job = state.get("sql_job")
            if route == "run_data_migration" and migration_job:
                return "run_data_migration"
            if route == "run_sql_conversion" and not migration_job and sql_job:
                return "run_sql_conversion"
            if route == "no_job" and not migration_job and not sql_job:
                return "no_job"
            if migration_job:
                logger.warning(
                    "[SupervisorDecision] invalid route corrected to run_data_migration "
                    f"(requested={route or '-'})"
                )
                return "run_data_migration"
            if sql_job:
                logger.warning(
                    "[SupervisorDecision] invalid route corrected to run_sql_conversion "
                    f"(requested={route or '-'})"
                )
                return "run_sql_conversion"
            if route and route != "no_job":
                logger.warning(
                    "[SupervisorDecision] invalid route corrected to no_job "
                    f"(requested={route})"
                )
            return "no_job"

        def run_data_migration(state: BatchSupervisorState) -> BatchSupervisorState:
            self._raise_if_batch_stop_requested()
            migration_job = state.get("migration_job") or {}
            map_id = int(migration_job["map_id"])
            self._console(f"run DB_MIGRATION map_id={map_id}")
            result = self._run_migration_job(config, map_id)
            return {
                "result": {
                    "job_executed": True,
                    "ok": bool(result.get("ok")),
                    "agent": "DB_MIGRATION",
                    "job_id": str(map_id),
                    "status": result.get("status"),
                    "message": result.get("message") or "Migration job finished.",
                    "error": result.get("error"),
                    "supervisor_tool": "run_data_migration",
                }
            }

        def run_sql_conversion(state: BatchSupervisorState) -> BatchSupervisorState:
            self._raise_if_batch_stop_requested()
            sql_job = state.get("sql_job") or {}
            selected_space = str(sql_job.get("space_nm") or "")
            selected_sql_id = str(sql_job.get("sql_id") or "")
            self._console(f"run SQL_CONVERSION space_nm={selected_space} sql_id={selected_sql_id}")
            result = self._run_sql_conversion_job(config, selected_space, selected_sql_id)
            return {
                "result": {
                    "job_executed": True,
                    "ok": bool(result.get("ok")),
                    "agent": "SQL_CONVERSION",
                    "job_id": f"{selected_space}/{selected_sql_id}",
                    "status": result.get("status"),
                    "message": result.get("message") or "SQL conversion job finished.",
                    "error": result.get("error"),
                    "supervisor_tool": "run_sql_conversion",
                }
            }

        def no_job(_: BatchSupervisorState) -> BatchSupervisorState:
            self._raise_if_batch_stop_requested()
            return {
                "result": {
                    "job_executed": False,
                    "ok": True,
                    "agent": None,
                    "job_id": None,
                    "status": "NO_JOB",
                    "message": "No pending migration or SQL conversion job found.",
                    "error": None,
                    "supervisor_tool": "no_job",
                }
            }

        workflow = StateGraph(BatchSupervisorState)
        workflow.add_node("poll_jobs", poll_jobs)
        workflow.add_node("supervisor_decide", supervisor_decide)
        workflow.add_node("run_data_migration", run_data_migration)
        workflow.add_node("run_sql_conversion", run_sql_conversion)
        workflow.add_node("no_job", no_job)
        workflow.add_edge(START, "poll_jobs")
        workflow.add_edge("poll_jobs", "supervisor_decide")
        workflow.add_conditional_edges(
            "supervisor_decide",
            route_after_decision,
            {
                "run_data_migration": "run_data_migration",
                "run_sql_conversion": "run_sql_conversion",
                "no_job": "no_job",
            },
        )
        workflow.add_edge("run_data_migration", END)
        workflow.add_edge("run_sql_conversion", END)
        workflow.add_edge("no_job", END)

        final_state = workflow.compile().invoke({})
        result = final_state.get("result")
        if not result:
            raise RuntimeError("Batch supervisor graph finished without a result.")
        return result


    def _run_migration_job(self, config: dict[str, Any], map_id: int) -> dict[str, Any]:
        self._apply_config(config)
        self._batch_runtime_config = config
        return self._mig__run_migration_job(
            map_id,
            {
                "action": "run_migration_job",
                "map_id": map_id,
                "max_attempts": config["migration_max_attempts"],
            },
        )
    def _run_sql_conversion_job(self, config: dict[str, Any], space_nm: str, sql_id: str) -> dict[str, Any]:
        self._apply_config(config)
        self._batch_runtime_config = config
        return self._sql_run_sql_conversion_job(
            sql_id,
            space_nm,
            {
                "action": "run_sql_conversion_job",
                "space_nm": space_nm,
                "sql_id": sql_id,
                "max_attempts": config["sql_conversion_max_attempts"],
            },
        )

    def _parse_supervisor_decision(self, raw_decision: str) -> dict[str, Any]:
        text = str(raw_decision or "").strip()
        if not text:
            logger.warning("[SupervisorDecision] empty LLM response")
            return {"route": "", "reason": "empty LLM response"}
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                logger.warning(f"[SupervisorDecision] failed to parse JSON: {raw_decision[:500]}")
                return {"route": "", "reason": "failed to parse JSON"}
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                logger.warning(f"[SupervisorDecision] failed to parse JSON object: {raw_decision[:500]}")
                return {"route": "", "reason": "failed to parse JSON object"}
        if not isinstance(parsed, dict):
            logger.warning(f"[SupervisorDecision] JSON is not an object: {raw_decision[:500]}")
            return {"route": "", "reason": "JSON is not an object"}
        return {
            "route": str(parsed.get("route") or "").strip(),
            "reason": str(parsed.get("reason") or "").strip(),
        }

    def _consume_chat_command(self, config: dict[str, Any]) -> str:
        """Consume one user command for this supervisor cycle.

        This mirrors the original SupervisorAgent behavior:
        runtime/chat_command.json is read once and deleted. Langflow chat_input
        is also treated as a one-cycle seed command, then cleared from the
        runtime config so it does not force the same job every loop.
        """
        inline_command = str(config.get("chat_input") or "").strip()
        if inline_command:
            config["chat_input"] = ""
            return inline_command
        return self._read_chat_command_file()

    def _read_chat_command_file(self) -> str:
        if not _COMMAND_FILE.exists():
            return ""
        try:
            data = json.loads(_COMMAND_FILE.read_text(encoding="utf-8"))
            _COMMAND_FILE.unlink(missing_ok=True)
            return str(data.get("command") or "").strip()
        except Exception:
            logger.exception(f"[Supervisor] failed to read chat command file: {_COMMAND_FILE}")
            return ""

    def _parse_chat_target(self, chat_command: str) -> dict[str, Any]:
        text = str(chat_command or "").strip()
        if not text:
            return {}

        target: dict[str, Any] = {}
        map_match = re.search(r"\bmap[\s_-]*id\s*[:=]?\s*(\d+)\b", text, flags=re.IGNORECASE)
        if not map_match:
            map_match = re.search(r"\bmap\s*[:=]?\s*(\d+)\b", text, flags=re.IGNORECASE)
        if map_match:
            target["map_id"] = int(map_match.group(1))

        sql_match = re.search(
            r"\bsql[\s_-]*id\s*[:=]?\s*([A-Za-z0-9_$#.\-]+)",
            text,
            flags=re.IGNORECASE,
        )
        if sql_match:
            target["sql_id"] = sql_match.group(1)

        space_match = re.search(
            r"\b(?:space[\s_-]*nm|namespace)\s*[:=]?\s*([A-Za-z0-9_$#.\-/]+)",
            text,
            flags=re.IGNORECASE,
        )
        if space_match:
            target["space_nm"] = space_match.group(1)

        return target

    def _poll_next_migration_job(self, config: dict[str, Any], map_id: int | None = None) -> dict[str, Any] | None:
        table = self._qualify_table("NEXT_MIG_INFO", config["system_schema"])
        params: list[Any] = []
        map_filter = ""
        if map_id is not None:
            map_filter = "AND MAP_ID = :1"
            params.append(int(map_id))
        sql = f"""
            SELECT MAP_ID, PRIORITY
            FROM (
                SELECT MAP_ID, PRIORITY
                FROM {table}
                WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                  AND STATUS IS NULL
                  {map_filter}
                ORDER BY PRIORITY ASC, MAP_ID ASC
            )
            WHERE ROWNUM <= 1
        """
        rows = self._query(config, sql, params)
        if not rows:
            return None
        return {"map_id": rows[0][0], "priority": rows[0][1]}

    def _count_pending_migration_jobs(self, config: dict[str, Any]) -> int:
        table = self._qualify_table("NEXT_MIG_INFO", config["system_schema"])
        sql = f"""
            SELECT COUNT(*)
            FROM {table}
            WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
              AND STATUS IS NULL
        """
        rows = self._query(config, sql)
        if not rows:
            return 0
        return int(rows[0][0] or 0)

    def _poll_next_sql_conversion_job(
        self,
        config: dict[str, Any],
        sql_id: str | None = None,
        space_nm: str | None = None,
    ) -> dict[str, Any] | None:
        table = self._qualify_table("NEXT_SQL_INFO", config["system_schema"])
        params: list[Any] = []
        filters = ["STATUS_CONVERSION IS NULL"]
        if sql_id:
            params.append(str(sql_id))
            filters.append(f"SQL_ID = :{len(params)}")
        if space_nm:
            params.append(str(space_nm))
            filters.append(f"SPACE_NM = :{len(params)}")
        where_clause = "\n                  AND ".join(filters)
        sql = f"""
            SELECT SPACE_NM, SQL_ID, PRIORITY
            FROM (
                SELECT SPACE_NM, SQL_ID, PRIORITY
                FROM {table}
                WHERE {where_clause}
                ORDER BY PRIORITY ASC NULLS LAST, UPD_TS NULLS FIRST, SPACE_NM, SQL_ID
            )
            WHERE ROWNUM <= 1
        """
        rows = self._query(config, sql, params)
        if not rows:
            return None
        return {"space_nm": self._to_text(rows[0][0]), "sql_id": self._to_text(rows[0][1]), "priority": rows[0][2]}

    def _apply_config(self, config: dict[str, Any]) -> None:
        for key, value in config.items():
            setattr(self, key, value)
        self.default_max_attempts = config["migration_max_attempts"]
    def _snapshot_config(self) -> dict[str, Any]:
        return {
            "run_yn": str(getattr(self, "run_yn", "") or "").strip().upper(),
            "db_host": str(getattr(self, "db_host", "") or DEFAULT_DB_CONFIG["db_host"]).strip(),
            "db_port": int(getattr(self, "db_port", None) or DEFAULT_DB_CONFIG["db_port"]),
            "db_service_name": str(getattr(self, "db_service_name", "") or DEFAULT_DB_CONFIG["db_service_name"]).strip(),
            "db_username": str(getattr(self, "db_username", "") or DEFAULT_DB_CONFIG["db_username"]).strip(),
            "db_password": self._secret_to_str(getattr(self, "db_password", None)) or str(DEFAULT_DB_CONFIG["db_password"]),
            "llm_base_url": str(getattr(self, "llm_base_url", "") or os.getenv("SMARTMIGRATE_LLM_BASE_URL", DEFAULT_LLM_CONFIG["llm_base_url"])).strip(),
            "llm_api_key": self._secret_to_str(getattr(self, "llm_api_key", None)) or os.getenv("SMARTMIGRATE_LLM_API_KEY", str(DEFAULT_LLM_CONFIG["llm_api_key"])),
            "llm_model": str(getattr(self, "llm_model", "") or os.getenv("SMARTMIGRATE_LLM_MODEL", str(DEFAULT_LLM_CONFIG["llm_model"]))).strip(),
            "llm_max_tokens": int(getattr(self, "llm_max_tokens", None) or os.getenv("SMARTMIGRATE_LLM_MAX_TOKENS", str(DEFAULT_LLM_CONFIG["llm_max_tokens"]))),
            "llm_timeout_seconds": int(getattr(self, "llm_timeout_seconds", None) or os.getenv("SMARTMIGRATE_LLM_TIMEOUT_SECONDS", str(DEFAULT_LLM_CONFIG["llm_timeout_seconds"]))),
            "chat_input": str(getattr(self, "chat_input", "") or os.getenv("SMARTMIGRATE_CHAT_INPUT", "")),
            "mig_sql_prompt": str(getattr(self, "mig_sql_prompt", "") or os.getenv("SMARTMIGRATE_MIG_SQL_PROMPT", "")),
            "verify_sql_prompt": str(getattr(self, "verify_sql_prompt", "") or os.getenv("SMARTMIGRATE_VERIFY_SQL_PROMPT", "")),
            "to_sql_prompt": str(getattr(self, "to_sql_prompt", "") or os.getenv("SMARTMIGRATE_TO_SQL_PROMPT", "")),
            "bind_sql_prompt": str(getattr(self, "bind_sql_prompt", "") or os.getenv("SMARTMIGRATE_BIND_SQL_PROMPT", "")),
            "test_sql_prompt": str(getattr(self, "test_sql_prompt", "") or os.getenv("SMARTMIGRATE_TEST_SQL_PROMPT", "")),
            "system_schema": str(DEFAULT_DB_CONFIG["system_schema"]).strip(),
            "source_schema": str(DEFAULT_DB_CONFIG["source_schema"]).strip(),
            "target_schema": str(DEFAULT_DB_CONFIG["target_schema"]).strip(),
            "migration_max_attempts": max(1, int(getattr(self, "migration_max_attempts", None) or 3)),
            "sql_conversion_max_attempts": max(1, int(getattr(self, "sql_conversion_max_attempts", None) or 3)),
            "no_job_sleep_seconds": 10,
            "error_sleep_seconds": max(1, int(getattr(self, "error_sleep_seconds", None) or 60)),
        }

    def _connect(self, config: dict[str, Any]):
        self._ensure_runtime_dependencies(config)
        import oracledb

        dsn = f"{config['db_host']}:{config['db_port']}/{config['db_service_name']}"
        return oracledb.connect(user=config["db_username"], password=config["db_password"], dsn=dsn)

    def _ensure_runtime_dependencies(self, config: dict[str, Any]) -> None:
        missing_packages: list[str] = []
        try:
            import langchain_core
        except ModuleNotFoundError:
            missing_packages.append("langchain-core")
        try:
            import langchain_openai
        except ModuleNotFoundError:
            missing_packages.append("langchain-openai")
        try:
            import langchain_community
        except ModuleNotFoundError:
            missing_packages.append("langchain-community")
        try:
            import langgraph
        except ModuleNotFoundError:
            missing_packages.append("langgraph")
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
        if not AUTO_INSTALL_MISSING_PACKAGES:
            raise ModuleNotFoundError("Missing packages: " + ", ".join(missing_packages))
        for package in missing_packages:
            self._pip_install(package)

    def _pip_install(self, package: str) -> None:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

    def _query(self, config: dict[str, Any], sql: str, params: list[Any] | None = None) -> list[tuple]:
        with self._connect(config) as conn:
            cur = conn.cursor()
            cur.execute(sql, params or [])
            return cur.fetchall()

    def _batch_control_table(self, config: dict[str, Any]) -> str:
        return self._qualify_table("NEXT_BATCH_CONTROL", config["system_schema"])

    def _try_acquire_batch_control(self, config: dict[str, Any], run_id: str) -> bool:
        """Acquire the single DB control row before entering the blocking loop.

        Memory state prevents duplicate starts inside one Langflow worker. This
        DB update is the real cross-process lock. If another worker is already
        RUNNING and its heartbeat is fresh, rowcount is 0 and this caller must
        not enter Supervisor_loop().
        """
        table = self._batch_control_table(config)
        heartbeat_timeout_seconds = 300
        with self._connect(config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET STATUS = 'RUNNING',
                       STOP_REQUESTED_YN = 'N',
                       RUN_ID = :1,
                       LOOP_NO = 0,
                       STARTED_AT = CURRENT_TIMESTAMP,
                       HEARTBEAT_AT = CURRENT_TIMESTAMP,
                       STOP_REQUESTED_AT = NULL,
                       STOPPED_AT = NULL,
                       UPDATED_AT = CURRENT_TIMESTAMP,
                       LAST_EVENT = 'START',
                       LAST_AGENT = NULL,
                       LAST_JOB_ID = NULL,
                       LAST_JOB_STATUS = NULL,
                       LAST_ERROR = NULL,
                       MESSAGE = 'Batch supervisor started.'
                 WHERE CONTROL_NAME = 'BATCH_AGENT'
                   AND (
                        UPPER(TRIM(STATUS)) <> 'RUNNING'
                        OR UPPER(TRIM(NVL(STOP_REQUESTED_YN, 'N'))) = 'Y'
                        OR HEARTBEAT_AT IS NULL
                        OR HEARTBEAT_AT < CURRENT_TIMESTAMP - NUMTODSINTERVAL(:2, 'SECOND')
                   )
                """,
                [run_id, heartbeat_timeout_seconds],
            )
            acquired = cur.rowcount == 1
            conn.commit()
        return acquired

    def _request_batch_stop(self, config: dict[str, Any]) -> None:
        table = self._batch_control_table(config)
        with self._connect(config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET STATUS = 'STOP_REQUESTED',
                       STOP_REQUESTED_YN = 'Y',
                       STOP_REQUESTED_AT = CURRENT_TIMESTAMP,
                       UPDATED_AT = CURRENT_TIMESTAMP,
                       LAST_EVENT = 'STOP_REQUESTED',
                       MESSAGE = 'Stop requested.'
                 WHERE CONTROL_NAME = 'BATCH_AGENT'
                """
            )
            conn.commit()

    def _read_batch_control(self, config: dict[str, Any]) -> dict[str, Any]:
        table = self._batch_control_table(config)
        rows = self._query(
            config,
            f"""
            SELECT STATUS, RUN_ID, STOP_REQUESTED_YN, LOOP_NO, STARTED_AT,
                   HEARTBEAT_AT, STOP_REQUESTED_AT, STOPPED_AT, UPDATED_AT,
                   LAST_EVENT, LAST_AGENT, LAST_JOB_ID, LAST_JOB_STATUS, MESSAGE
              FROM {table}
             WHERE CONTROL_NAME = 'BATCH_AGENT'
            """,
        )
        if not rows:
            return {"exists": False}
        row = rows[0]
        return {
            "exists": True,
            "status": self._to_text(row[0]).upper(),
            "run_id": self._to_text(row[1]),
            "stop_requested_yn": self._to_text(row[2]).upper(),
            "loop_no": int(row[3] or 0),
            "started_at": row[4],
            "heartbeat_at": row[5],
            "stop_requested_at": row[6],
            "stopped_at": row[7],
            "updated_at": row[8],
            "last_event": self._to_text(row[9]),
            "last_agent": self._to_text(row[10]),
            "last_job_id": self._to_text(row[11]),
            "last_job_status": self._to_text(row[12]),
            "message": self._to_text(row[13]),
        }

    def _batch_should_continue(self, config: dict[str, Any], run_id: str) -> tuple[bool, str]:
        control = self._read_batch_control(config)
        if not control.get("exists"):
            return False, "NEXT_BATCH_CONTROL row BATCH_AGENT does not exist."
        status = str(control.get("status") or "").upper()
        stop_requested = str(control.get("stop_requested_yn") or "").upper() == "Y"
        if stop_requested or status == "STOP_REQUESTED":
            return False, "Batch stop requested in NEXT_BATCH_CONTROL."
        if status != "RUNNING":
            return False, f"Batch control status is {status or 'NULL'}."
        return True, "running"

    def _update_batch_control_heartbeat(
        self,
        config: dict[str, Any],
        run_id: str,
        loop_no: int,
        event_type: str,
        agent_name: Any = None,
        job_id: Any = None,
        job_status: Any = None,
        error_message: Any = None,
        message: Any = None,
    ) -> None:
        table = self._batch_control_table(config)
        with self._connect(config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET LOOP_NO = :1,
                       HEARTBEAT_AT = CURRENT_TIMESTAMP,
                       UPDATED_AT = CURRENT_TIMESTAMP,
                       LAST_EVENT = :2,
                       LAST_AGENT = :3,
                       LAST_JOB_ID = :4,
                       LAST_JOB_STATUS = :5,
                       LAST_ERROR = :6,
                       MESSAGE = :7
                 WHERE CONTROL_NAME = 'BATCH_AGENT'
                """,
                [
                    int(loop_no or 0),
                    str(event_type or "")[:50],
                    str(agent_name or "")[:50] if agent_name else None,
                    str(job_id or "")[:200] if job_id else None,
                    str(job_status or "")[:50] if job_status else None,
                    str(error_message or "") if error_message else None,
                    str(message or "")[:1000] if message else None,
                ],
            )
            conn.commit()

    def _mark_batch_control_stopped(self, config: dict[str, Any], run_id: str) -> None:
        table = self._batch_control_table(config)
        with self._connect(config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                   SET STATUS = 'STOPPED',
                       STOP_REQUESTED_YN = 'N',
                       STOPPED_AT = CURRENT_TIMESTAMP,
                       UPDATED_AT = CURRENT_TIMESTAMP,
                       LAST_EVENT = 'STOPPED',
                       MESSAGE = 'Batch supervisor stopped.'
                 WHERE CONTROL_NAME = 'BATCH_AGENT'
                """,
            )
            conn.commit()

    def _write_batch_log_safe(
        self,
        config: dict[str, Any],
        run_id: Any,
        loop_no: int,
        event_type: str,
        agent_name: Any = None,
        job_id: Any = None,
        job_status: Any = None,
        message: Any = None,
        error_message: Any = None,
        sleep_seconds: int | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        try:
            self._write_batch_log(
                config,
                run_id=run_id,
                loop_no=loop_no,
                event_type=event_type,
                agent_name=agent_name,
                job_id=job_id,
                job_status=job_status,
                message=message,
                error_message=error_message,
                sleep_seconds=sleep_seconds,
                elapsed_seconds=elapsed_seconds,
            )
        except Exception:
            logger.exception(
                "[BatchSupervisor] failed to write NEXT_BATCH_LOG "
                f"event_type={event_type} run_id={run_id} loop_no={loop_no}"
            )

    def _write_batch_log(
        self,
        config: dict[str, Any],
        run_id: Any,
        loop_no: int,
        event_type: str,
        agent_name: Any = None,
        job_id: Any = None,
        job_status: Any = None,
        message: Any = None,
        error_message: Any = None,
        sleep_seconds: int | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        table = self._qualify_table("NEXT_BATCH_LOG", config["system_schema"])
        with self._connect(config) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {table} (
                    RUN_ID, LOOP_NO, EVENT_TYPE, AGENT_NAME, JOB_ID, JOB_STATUS,
                    MESSAGE, ERROR_MESSAGE, SLEEP_SECONDS, STARTED_AT, FINISHED_AT, ELAPSED_SECONDS
                ) VALUES (
                    :1, :2, :3, :4, :5, :6,
                    :7, :8, :9, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :10
                )
                """,
                [
                    str(run_id or "")[:64],
                    int(loop_no or 0),
                    str(event_type or "")[:30],
                    str(agent_name or "")[:50] if agent_name else None,
                    str(job_id or "")[:200] if job_id else None,
                    str(job_status or "")[:50] if job_status else None,
                    str(message or "")[:1000] if message else None,
                    str(error_message or "") if error_message else None,
                    sleep_seconds,
                    elapsed_seconds,
                ],
            )
            conn.commit()

    def _interruptible_sleep(self, seconds: int, config: dict[str, Any] | None = None, run_id: str | None = None) -> None:
        deadline = time.time() + max(0, int(seconds))
        while time.time() < deadline:
            if config and run_id:
                should_continue, reason = self._batch_should_continue(config, run_id)
                if not should_continue:
                    raise InterruptedError(reason)
            elif self._run_yn_value(config) != "Y":
                break
            time.sleep(min(1.0, max(0.0, deadline - time.time())))

    def _qualify_table(self, table_name: str, schema: str | None) -> str:
        clean = str(table_name or "").strip().upper()
        clean_schema = str(schema or "").strip().upper()
        if not clean:
            raise ValueError("table_name is empty")
        if "." in clean or not clean_schema:
            return clean
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean_schema):
            raise ValueError(f"Invalid schema: {clean_schema}")
        return f"{clean_schema}.{clean}"

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    def _to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "t", "y", "yes", "on"}

    # action="get_table_ddl": Oracle 테이블 컬럼 메타데이터를 조회한다.
    def _mig__get_table_ddl(self, table_name: Any, schema: Any = None) -> dict[str, Any]:
        clean_table = str(table_name or "").strip().upper()
        clean_schema = str(schema or "").strip().upper()
        if not clean_table:
            raise ValueError("table_name is required")
        if "." in clean_table and not clean_schema:
            clean_schema, clean_table = clean_table.split(".", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean_table):
            raise ValueError(f"Invalid table_name: {clean_table}")
        if clean_schema:
            if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean_schema):
                raise ValueError(f"Invalid schema: {clean_schema}")
            query = f"""
                SELECT COLUMN_ID, COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION,
                       DATA_SCALE, NULLABLE
                FROM ALL_TAB_COLUMNS
                WHERE OWNER = '{clean_schema}'
                  AND TABLE_NAME = '{clean_table}'
                ORDER BY COLUMN_ID
            """
        else:
            query = f"""
                SELECT COLUMN_ID, COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION,
                       DATA_SCALE, NULLABLE
                FROM USER_TAB_COLUMNS
                WHERE TABLE_NAME = '{clean_table}'
                ORDER BY COLUMN_ID
            """
        rows = self._mig__normalize_query_rows(self._mig__get_db().run(query, include_columns=True))

        def column_value(row: dict[str, Any], key: str) -> Any:
            if key in row:
                return row[key]
            for candidate_key, value in row.items():
                if str(candidate_key).upper() == key.upper():
                    return value
            return None

        columns = [
            {
                "column_id": column_value(row, "COLUMN_ID"),
                "column_name": self._mig__to_text(column_value(row, "COLUMN_NAME")),
                "data_type": self._mig__to_text(column_value(row, "DATA_TYPE")),
                "data_length": column_value(row, "DATA_LENGTH"),
                "data_precision": column_value(row, "DATA_PRECISION"),
                "data_scale": column_value(row, "DATA_SCALE"),
                "nullable": self._mig__to_text(column_value(row, "NULLABLE")),
            }
            for row in rows
        ]
        return {
            "ok": True,
            "schema": clean_schema or "CURRENT_USER",
            "table_name": clean_table,
            "column_count": len(columns),
            "columns": columns,
        }

    # action="generate_mig_sql": MIG_SQL을 생성한다.
    def _mig__generate_mig_sql(self, map_id: Any, command: dict[str, Any]) -> dict[str, Any]:
        if map_id is None or str(map_id).strip() == "":
            raise ValueError("map_id is required")
        map_id = int(map_id)
        job = self._mig__load_job(map_id)
        if not job:
            return {"ok": False, "map_id": map_id, "error": "job not found"}

        user_edited = str(job.get("user_edited") or "").strip().upper() == "Y"
        existing_mig_sql = str(job.get("mig_sql") or "").strip()
        if user_edited:
            if existing_mig_sql:
                return {
                    "ok": True,
                    "map_id": map_id,
                    "status": "MIG_SQL_SKIPPED_USER_EDITED",
                    "message": "USER_EDITED=Y. Existing MIG_SQL was preserved.",
                    "generation_source": "user_edited",
                    "mig_sql": existing_mig_sql,
                }
            return {"ok": False, "map_id": map_id, "error": "USER_EDITED=Y but MIG_SQL is empty"}
        dep = self._mig__check_dependencies(job)
        if not dep["ok"]:
            return {"ok": False, "map_id": map_id, "status": dep["status"], "message": dep["message"]}
        details = self._mig__load_details(map_id)
        if not details:
            return {"ok": False, "map_id": map_id, "error": "No mapping details found"}

        generation_source = "llm"
        llm_error = ""
        try:
            mig_sql_prompt = str(self.mig_sql_prompt or "").strip()
            if not mig_sql_prompt:
                raise ValueError("MIG SQL Prompt input is required for SQL generation")
            prompt = self._mig__render_sql_prompt(
                template=mig_sql_prompt,
                job=job,
                details=details,
                command=command,
            )
            mig_sql = self._mig__sanitize_migration_sql(
                self._mig__extract_sql(self._mig__call_llm(prompt), expected="insert", key="migration_sql")
            )
        except Exception as exc:
            llm_error = str(exc)
            return {"ok": False, "map_id": map_id, "error": llm_error, "generation_source": generation_source}
        return {
            "ok": True,
            "map_id": map_id,
            "status": "MIG_SQL_GENERATED",
            "generation_source": generation_source,
            "llm_error": llm_error,
            "mig_sql": mig_sql,
        }

    # action="generate_verify_sql": VERIFY_SQL을 생성한다.
    def _mig__generate_verify_sql(self, map_id: Any, command: dict[str, Any]) -> dict[str, Any]:
        if map_id is None or str(map_id).strip() == "":
            raise ValueError("map_id is required")
        map_id = int(map_id)
        job = self._mig__load_job(map_id)
        if not job:
            return {"ok": False, "map_id": map_id, "error": "job not found"}

        user_edited = str(job.get("user_edited") or "").strip().upper() == "Y"
        existing_mig_sql = str(job.get("mig_sql") or "").strip()
        existing_verify_sql = str(job.get("verify_sql") or "").strip()
        if user_edited:
            if not existing_mig_sql:
                return {"ok": False, "map_id": map_id, "error": "USER_EDITED=Y but MIG_SQL is empty"}
            if existing_verify_sql:
                return {
                    "ok": True,
                    "map_id": map_id,
                    "status": "VERIFY_SQL_SKIPPED_USER_EDITED",
                    "message": "USER_EDITED=Y. Existing VERIFY_SQL was preserved.",
                    "generation_source": "user_edited",
                    "verify_sql": existing_verify_sql,
                }

        dep = self._mig__check_dependencies(job)
        if not dep["ok"]:
            return {"ok": False, "map_id": map_id, "status": dep["status"], "message": dep["message"]}

        details = self._mig__load_details(map_id)
        generation_source = "llm"
        llm_error = ""

        try:
            verify_sql_prompt = str(self.verify_sql_prompt or "").strip()
            if not verify_sql_prompt:
                raise ValueError("VERIFY SQL Prompt input is required for SQL generation")
            prompt = self._mig__render_sql_prompt(
                template=verify_sql_prompt,
                job=job,
                details=details,
                command=command,
            )
            verify_sql = self._mig__sanitize_verify_sql(
                self._mig__extract_sql(self._mig__call_llm(prompt), expected="select", key="verification_sql")
            )
        except Exception as exc:
            llm_error = str(exc)
            return {"ok": False, "map_id": map_id, "error": llm_error, "generation_source": generation_source}

        return {
            "ok": True,
            "map_id": map_id,
            "status": "VERIFY_SQL_GENERATED",
            "generation_source": generation_source,
            "llm_error": llm_error,
            "verify_sql": verify_sql,
        }

    # action="run_migration_job": SQL 생성, 실행, 검증까지 전체 마이그레이션 사이클을 수행한다.
    def _mig__run_migration_job(self, map_id: Any, command: dict[str, Any]) -> dict[str, Any]:

        if map_id is None or str(map_id).strip() == "":
            raise ValueError("map_id is required")
        map_id = int(map_id)

        started = time.perf_counter()
        max_attempts = max(1, int(command.get("max_attempts") or self.default_max_attempts or 1))

        job = self._mig__load_job(map_id)
        if not job:
            return {"ok": False, "map_id": map_id, "error": "job not found"}

        if str(job.get("use_yn") or "").upper() != "Y":
            return {"ok": False, "map_id": map_id, "status": "SKIP", "error": "USE_YN is not Y"}

        current_status = str(job.get("status") or "").strip().upper()
        if current_status == "PASS":
            return {"ok": True, "map_id": map_id, "status": "PASS", "message": "Job already passed"}
        if current_status:
            return {
                "ok": False,
                "map_id": map_id,
                "status": current_status,
                "error": "Full migration is allowed only when STATUS is NULL.",
            }

        dep = self._mig__check_dependencies(job)
        if not dep["ok"]:
            final_status = str(dep.get("status") or "WAITING")
            self._mig__write_log(map_id, "DEPENDENCY", "WARN", "DEP_CHECK", final_status, dep["message"])
            return {"ok": True, "map_id": map_id, "status": final_status, "message": dep["message"]}

        steps: list[dict[str, Any]] = []

        last_mig_sql = str(job.get("mig_sql") or "")
        last_verify_sql = str(job.get("verify_sql") or "")
        last_retry_count = 0

        try:
            self._raise_if_batch_stop_requested()
            job = self._mig__load_job(map_id) or job
            user_edited = str(job.get("user_edited") or "").upper() == "Y"

            last_failure: dict[str, Any] = {}
            mig_executed = False
            verify_sql_executed = False

            for attempt in range(1, max_attempts + 1):
                self._raise_if_batch_stop_requested()
                retry_count = attempt - 1
                last_retry_count = retry_count

                job = self._mig__load_job(map_id) or job
                user_edited = str(job.get("user_edited") or "").upper() == "Y"

                if not mig_executed:
                    self._raise_if_batch_stop_requested()
                    if user_edited:
                        mig_sql = str(job.get("mig_sql") or "").strip()
                        if not mig_sql:
                            raise ValueError("USER_EDITED=Y but MIG_SQL is empty")
                        last_mig_sql = mig_sql
                        steps.append({"step": "generate_mig_sql", "attempt": attempt, "status": "SKIPPED_USER_EDITED"})
                    else:
                        mig_command = {
                            "retry_count": retry_count,
                            "last_error": last_failure.get("error", ""),
                            "last_sql": last_mig_sql,
                        }
                        mig_result = self._mig__generate_mig_sql(map_id, mig_command)
                        self._raise_if_batch_stop_requested()
                        steps.append({"step": "generate_mig_sql", "attempt": attempt, **self._mig__summary_result(mig_result)})
                        if not mig_result.get("ok"):
                            last_failure = {"status": "FAIL-INSERT", "error": mig_result.get("error") or "MIG_SQL generation failed"}
                            self._mig__write_log(map_id, "ROW_ERROR", "WARN", "RETRY" if retry_count > 0 else "GENERATE_MIG_SQL", "FAIL-INSERT", str(last_failure["error"])[:3900], retry_count)
                            if attempt < max_attempts:
                                continue
                            break

                        last_mig_sql = str(mig_result.get("mig_sql") or "")
                        self._mig__write_log(
                            map_id,
                            "GENERATE_SQL",
                            "INFO",
                            "GENERATE_MIG_SQL",
                            "PASS",
                            "MIG_SQL generated",
                            retry_count,
                            last_mig_sql,
                        )

                    try:
                        self._raise_if_batch_stop_requested()
                        job = {**job, "mig_sql": last_mig_sql}
                        mig_sql = self._mig__sanitize_migration_sql(str(job.get("mig_sql") or ""))
                        if str(job.get("trunc_yn") or "").upper() == "Y":
                            self._mig__truncate_target(job)
                            self._mig__write_log(map_id, "EXECUTE_SQL", "INFO", "TRUNCATE", "PASS", "Target table truncated", retry_count)
                        affected_rows = self._mig__execute_sql_script(mig_sql)
                        if affected_rows <= 0:
                            raise ValueError("Migration SQL affected 0 rows")
                        mig_exec_result = {
                            "ok": True,
                            "map_id": map_id,
                            "status": "SUCCESS-MIG",
                            "message": "Migration SQL executed",
                            "affected_rows": affected_rows,
                            "mig_sql": mig_sql,
                        }
                        steps.append({"step": "execute_mig_sql", "attempt": attempt, **self._mig__summary_result(mig_exec_result)})
                        mig_executed = True
                        self._raise_if_batch_stop_requested()
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        last_failure = {"status": "FAIL-INSERT", "error": str(exc)}
                        steps.append({"step": "execute_mig_sql", "attempt": attempt, "ok": False, **last_failure})
                        self._mig__write_log(map_id, "ROW_ERROR", "WARN", "RETRY" if retry_count > 0 else "SQL_EXEC", "FAIL-INSERT", str(exc)[:3900], retry_count, str(job.get("mig_sql") or ""))
                        if attempt < max_attempts:
                            continue
                        break

                if not verify_sql_executed:
                    self._raise_if_batch_stop_requested()
                    job = self._mig__load_job(map_id) or job
                    user_edited = str(job.get("user_edited") or "").upper() == "Y"
                    verify_sql = str(job.get("verify_sql") or "").strip()

                    if user_edited and verify_sql:
                        last_verify_sql = verify_sql
                        steps.append({"step": "generate_verify_sql", "attempt": attempt, "status": "SKIPPED_USER_EDITED"})
                    else:
                        verify_command = {
                            "retry_count": retry_count,
                            "last_error": last_failure.get("error", ""),
                            "last_sql": last_verify_sql,
                        }
                        verify_result = self._mig__generate_verify_sql(map_id, verify_command)
                        self._raise_if_batch_stop_requested()
                        steps.append({"step": "generate_verify_sql", "attempt": attempt, **self._mig__summary_result(verify_result)})

                        if not verify_result.get("ok"):
                            last_failure = {"status": "FAIL-TEST", "error": verify_result.get("error") or "VERIFY_SQL generation failed"}
                            self._mig__write_log(map_id, "ROW_ERROR", "WARN", "RETRY" if retry_count > 0 else "GENERATE_VERIFY_SQL", "FAIL-TEST", str(last_failure["error"])[:3900], retry_count)
                            if attempt < max_attempts:
                                continue
                            break

                        last_verify_sql = str(verify_result.get("verify_sql") or "")
                        self._mig__write_log(
                            map_id,
                            "GENERATE_SQL",
                            "INFO",
                            "GENERATE_VERIFY_SQL",
                            "PASS",
                            "VERIFY_SQL generated",
                            retry_count,
                            last_verify_sql,
                        )

                    try:
                        self._raise_if_batch_stop_requested()
                        job = {**job, "verify_sql": last_verify_sql}
                        verify_sql = self._mig__sanitize_verify_sql(str(job.get("verify_sql") or ""))
                        verify_ok, verify_message, rows = self._mig__execute_verify_sql_with_rows(verify_sql)
                        verify_exec_result = {
                            "ok": verify_ok,
                            "map_id": map_id,
                            "status": "PASS" if verify_ok else "FAIL-TEST",
                            "message": verify_message,
                            "verify_sql": verify_sql,
                            "result_rows": rows,
                        }
                        steps.append({"step": "execute_verify_sql", "attempt": attempt, **self._mig__summary_result(verify_exec_result)})

                        if verify_exec_result.get("ok"):
                            verify_sql_executed = True
                            elapsed = int(time.perf_counter() - started)
                            self._mig__save_final_sql(map_id, last_mig_sql, last_verify_sql)
                            self._mig__update_job_status(map_id, "PASS", elapsed, retry_count)
                            self._mig__write_log(map_id, "VERIFY_SQL", "INFO", "VERIFY", "PASS", "Migration Success", retry_count, verify_exec_result.get("verify_sql"))
                            return {
                                "ok": True,
                                "map_id": map_id,
                                "status": "PASS",
                                "message": "Migration completed",
                                "elapsed_seconds": elapsed,
                                "retry_count": retry_count,
                                "steps": steps,
                            }

                        self._raise_if_batch_stop_requested()
                        last_failure = {"status": "FAIL-TEST", "error": verify_exec_result.get("message") or "Verification failed"}
                        self._mig__write_log(map_id, "ROW_ERROR", "WARN", "RETRY" if retry_count > 0 else "VERIFY", "FAIL-TEST", str(last_failure["error"])[:3900], retry_count, verify_exec_result.get("verify_sql"))
                        if attempt < max_attempts:
                            continue
                        break
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        last_failure = {"status": "FAIL-TEST", "error": str(exc)}
                        steps.append({"step": "execute_verify_sql", "attempt": attempt, "ok": False, **last_failure})
                        self._mig__write_log(map_id, "ROW_ERROR", "WARN", "RETRY" if retry_count > 0 else "VERIFY", "FAIL-TEST", str(exc)[:3900], retry_count, str(job.get("verify_sql") or ""))
                        if attempt < max_attempts:
                            continue
                        break

            final_status = str(last_failure.get("status") or "FAIL")
            elapsed = int(time.perf_counter() - started)

            self._mig__save_final_sql(map_id, last_mig_sql, last_verify_sql)
            self._mig__update_job_status(map_id, final_status, elapsed, last_retry_count)
            self._mig__write_log(
                map_id,
                "JOB_FAIL",
                "ERROR",
                "FINAL",
                final_status,
                str(last_failure.get("error") or "Max attempts reached")[:3900],
                last_retry_count,
                last_verify_sql if final_status == "FAIL-TEST" else last_mig_sql,
            )
            return {
                "ok": False,
                "map_id": map_id,
                "status": final_status,
                "error": last_failure.get("error") or "Max attempts reached",
                "elapsed_seconds": elapsed,
                "retry_count": last_retry_count,
                "steps": steps,
            }
        except InterruptedError as exc:
            elapsed = int(time.perf_counter() - started)
            self._mig__save_final_sql(map_id, last_mig_sql, last_verify_sql)
            self._mig__update_job_status(map_id, "STOPPED", elapsed, last_retry_count)
            self._mig__write_log(map_id, "JOB_STOPPED", "WARN", "STOP_REQUESTED", "STOPPED", str(exc)[:3900], last_retry_count, last_verify_sql or last_mig_sql)
            return {
                "ok": False,
                "map_id": map_id,
                "status": "STOPPED",
                "error": str(exc),
                "elapsed_seconds": elapsed,
                "retry_count": last_retry_count,
                "steps": steps,
            }
        except Exception as exc:
            elapsed = int(time.perf_counter() - started)
            self._mig__save_final_sql(map_id, last_mig_sql, last_verify_sql)
            self._mig__update_job_status(map_id, "FAIL", elapsed, int(job.get("retry_count") or 0))
            self._mig__write_log(map_id, "ROW_ERROR", "ERROR", "RUN_FULL", "FAIL", str(exc)[:3900])
            return {
                "ok": False,
                "map_id": map_id,
                "status": "FAIL",
                "error": str(exc),
                "elapsed_seconds": elapsed,
                "steps": steps,
            }

    # ======================================================================
    # 공통 코드
    # ======================================================================
    # DB 입력값으로 Oracle SQLAlchemy connection string을 만든다.
    def _mig__connection_string(self) -> str:
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

    # 같은 DB 접속 정보는 SQLDatabase 인스턴스를 캐시해 재사용한다.
    def _mig__get_db(self):
        self._mig__ensure_runtime_dependencies()
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
            self._db_cache[cache_key] = SQLDatabase.from_uri(self._mig__connection_string())
        self.db = self._db_cache[cache_key]
        return self.db

    # DB 연결에 필요한 런타임 패키지를 확인한다.
    def _mig__ensure_runtime_dependencies(self) -> None:
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
        if not AUTO_INSTALL_MISSING_PACKAGES:
            raise ModuleNotFoundError("Missing packages: " + ", ".join(missing_packages))
        for package in missing_packages:
            self._mig__pip_install(package)

    def _mig__pip_install(self, package: str) -> None:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

    def _mig__post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
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

    def _mig__normalize_query_rows(self, raw: Any) -> list[dict[str, Any]]:
        if raw is None or raw == "":
            return []
        if isinstance(raw, list):
            if not raw:
                return []
            if isinstance(raw[0], dict):
                return raw
            return [{str(i): value for i, value in enumerate(row)} for row in raw]
        if isinstance(raw, tuple):
            return [{str(i): value for i, value in enumerate(raw)}]
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return []
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                return [{"text": text}]
            return self._mig__normalize_query_rows(parsed)
        return [{"value": raw}]

    @contextmanager
    def _mig__connect(self):
        db = self._mig__get_db()
        engine = getattr(db, "_engine", None) or getattr(db, "engine", None)
        if engine is None:
            raise ValueError("SQLDatabase engine is not available")
        conn = engine.raw_connection()
        try:
            yield conn
        finally:
            conn.close()

    def _mig__render_sql_prompt(
        self,
        template: str,
        job: dict[str, Any],
        details: list[dict[str, Any]],
        command: dict[str, Any],
    ) -> str:
        source_context = self._mig__build_source_context(job)
        to_table = self._mig__qualify_table(job.get("to_table", ""), self.target_schema)
        from_table = source_context["from_table"]
        mapping_info = self._mig__format_mapping_info(details)
        ddl_info_block = self._mig__build_ddl_info_block(from_table, to_table)
        last_error = str(command.get("last_error") or "").strip()
        last_sql = str(command.get("last_sql") or "").strip()
        retry_context = self._mig__build_retry_context(last_error, last_sql, command.get("retry_count"))
        rendered = str(template or "")
        prompt_values = {
            "ddl_info_block": ddl_info_block,
            "from_table": from_table,
            "to_table": to_table,
            "mapping_info": mapping_info,
            "condition": str(job.get("condition") or "").strip(),
            "source_kind": source_context["source_kind"],
            "source_query": source_context["source_query"],
            "source_from_clause": source_context["source_from_clause"],
            "complex_source_note": source_context["complex_source_note"],
            "retry_context": retry_context,
            "last_error": last_error,
            "last_sql": last_sql,
        }
        for key, value in prompt_values.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered

    def _mig__build_retry_context(self, last_error: str, last_sql: str, retry_count: Any = None) -> str:
        if not last_error and not last_sql:
            return ""
        retry_label = ""
        if retry_count is not None:
            retry_label = f"Retry count: {retry_count}\n"
        return (
            "[Retry context]\n"
            f"{retry_label}"
            f"Previous error:\n{last_error or '(none)'}\n\n"
            f"Previous SQL:\n{last_sql or '(none)'}\n\n"
            "Regenerate SQL by fixing the previous error. Do not repeat the same failing SQL.\n"
            "If the previous SQL contains duplicate WHERE clauses such as WHERE WHERE, remove the duplicate keyword.\n"
            "When applying the source filter condition, add WHERE only if the condition text does not already start with WHERE."
        )

    def _mig__format_mapping_info(self, details: list[dict[str, Any]]) -> str:
        lines = []
        for detail in details:
            fr_col = str(detail.get("fr_col") or "").strip()
            to_col = str(detail.get("to_col") or "").strip()
            if to_col:
                lines.append(f"  - {fr_col} -> {to_col}")
            else:
                lines.append(f"  - {fr_col} -> <skip target column; source expression may be used only as part of another mapped expression>")
        return "\n".join(lines) if lines else "  - No mapping details"

    def _mig__build_ddl_info_block(self, from_table: str, to_table: str) -> str:
        blocks = ["[DDL information]"]
        for label, table_name in [("Source", from_table), ("Target", to_table)]:
            try:
                columns = self._mig__table_columns_for_prompt(table_name)
            except Exception as exc:
                columns = f"Unable to load columns: {exc}"
            blocks.append(f"- {label} {table_name}:\n{columns}")
        return "\n".join(blocks)

    def _mig__build_source_context(self, job: dict[str, Any]) -> dict[str, str]:
        map_type = str(job.get("map_type") or "").strip().upper()
        raw_source = str(job.get("fr_table") or "").strip()
        qualified_source = raw_source
        source_schema = str(self.source_schema or "").strip().upper()
        if source_schema:
            if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", source_schema):
                raise ValueError(f"Invalid source_schema: {source_schema}")
            join_parts = re.split(r"\b(?:(?:LEFT|RIGHT|FULL|INNER|CROSS)\s+(?:OUTER\s+)?)?JOIN\b", raw_source, flags=re.I)
            source_tables: list[str] = []
            for part in join_parts:
                before_on = re.split(r"\bON\b", part, flags=re.I)[0].strip()
                tokens = before_on.split()
                if tokens and tokens[0].upper() not in {"SELECT", "WITH", "FROM", "("}:
                    source_tables.append(tokens[0])
            for table in sorted(set(source_tables), key=len, reverse=True):
                if "." in table:
                    continue
                qualified_source = re.sub(rf"(?<![.\w]){re.escape(table)}(?![.\w])", f"{source_schema}.{table}", qualified_source)
        if map_type == "COMPLEX":
            source_query = str(qualified_source or "").strip()
            while source_query.endswith(";"):
                source_query = source_query[:-1].rstrip()
            source_from_clause = f"(\n{source_query}\n) SRC"
            return {
                "source_kind": "COMPLEX_QUERY",
                "source_query": source_query,
                "source_from_clause": source_from_clause,
                "from_table": source_from_clause,
                "complex_source_note": (
                    "MAP_TYPE=COMPLEX. FR_TABLE is a complete source SELECT/WITH query, not a physical table. "
                    "Use it as an inline view exactly once in the FROM clause, and reference mapped FR_COL values from alias SRC. "
                    "Do not rebuild the source query or search for physical source columns outside this query."
                ),
            }
        return {
            "source_kind": "TABLE_OR_JOIN",
            "source_query": qualified_source,
            "source_from_clause": qualified_source,
            "from_table": qualified_source,
            "complex_source_note": "",
        }

    def _mig__table_columns_for_prompt(self, table_name: str) -> str:
        clean = str(table_name or "").strip()
        if not clean or any(token in clean.upper() for token in [" JOIN ", " SELECT ", " WITH "]):
            return "Complex source expression. Use mapping rules as the source of truth."
        schema = None
        table = clean
        if "." in clean:
            schema, table = clean.split(".", 1)
        meta = self._mig__get_table_ddl(table, schema)
        columns = meta.get("columns", [])
        if not columns:
            return "No columns found."
        return "\n".join(
            f"  - {col.get('column_name')} {col.get('data_type')}"
            + (f"({col.get('data_precision')},{col.get('data_scale')})" if col.get("data_precision") else f"({col.get('data_length')})")
            + f" nullable={col.get('nullable')}"
            for col in columns[:200]
        )

    def _mig__call_llm(self, prompt: str) -> str:
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
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        data = self._mig__post_json(url, payload, {"Authorization": f"Bearer {api_key}"})
        return str(data["choices"][0]["message"].get("content", ""))

    def _mig__extract_sql(self, value: Any, expected: str, key: str | None = None) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("LLM returned empty SQL")
        fence = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.I | re.S)
        if fence:
            text = fence.group(1).strip()
        if key:
            parsed = self._mig__parse_llm_json(text)
            text = str(parsed.get(key) or "").strip()
        text = text.rstrip(";").strip()
        first_word = text.split(None, 1)[0].upper() if text.split(None, 1) else ""
        allowed = {"insert": {"INSERT"}, "select": {"SELECT", "WITH"}}
        if first_word not in allowed.get(expected, set()):
            raise ValueError(f"Expected {expected.upper()} SQL but got: {first_word or text[:40]}")
        return text

    def _mig__parse_llm_json(self, text: str) -> dict[str, Any]:
        clean = str(text or "").strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", clean, flags=re.I | re.S)
        if fence:
            clean = fence.group(1).strip()
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", clean, flags=re.S)
            if not match:
                raise
            parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise ValueError("LLM JSON response must be an object")
        return parsed

    def _mig__sanitize_migration_sql(self, sql: str) -> str:
        cleaned = str(sql or "").strip().rstrip(";").strip()
        if not cleaned:
            raise ValueError("MIG_SQL is empty")
        upper = cleaned.upper()
        forbidden = ["TRUNCATE", "COMMIT", "ROLLBACK", "DELETE", "UPDATE", "MERGE", "DROP", "ALTER"]
        for token in forbidden:
            if re.search(rf"\b{token}\b", upper):
                raise ValueError(f"MIG_SQL must not contain {token}")
        statements = self._mig__split_sql_script(cleaned)
        if len(statements) != 1:
            raise ValueError("MIG_SQL must contain exactly one INSERT statement")
        statement = statements[0].strip().rstrip(";").strip()
        if not statement.upper().startswith("INSERT"):
            raise ValueError("MIG_SQL must start with INSERT")
        return statement

    def _mig__sanitize_verify_sql(self, sql: str) -> str:
        cleaned = str(sql or "").strip().rstrip(";").strip()
        if not cleaned:
            raise ValueError("VERIFY_SQL is empty")
        upper = cleaned.upper()
        forbidden = ["TRUNCATE", "COMMIT", "ROLLBACK", "INSERT", "DELETE", "UPDATE", "MERGE", "DROP", "ALTER"]
        for token in forbidden:
            if re.search(rf"\b{token}\b", upper):
                raise ValueError(f"VERIFY_SQL must not contain {token}")
        statements = self._mig__split_sql_script(cleaned)
        if len(statements) != 1:
            raise ValueError("VERIFY_SQL must contain exactly one SELECT statement")
        statement = statements[0].strip().rstrip(";").strip()
        first_word = statement.split(None, 1)[0].upper() if statement.split(None, 1) else ""
        if first_word not in {"SELECT", "WITH"}:
            raise ValueError("VERIFY_SQL must start with SELECT or WITH")
        return statement

    # NEXT_MIG_INFO에서 map_id에 해당하는 작업 row를 조회한다.
    def _mig__load_job(self, map_id: int) -> dict[str, Any] | None:
        map_table = self._mig__qualify_table("NEXT_MIG_INFO", self.system_schema)
        with self._mig__connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT MAP_ID, MAP_TYPE, FR_TABLE, TO_TABLE, USE_YN, TRUNC_YN,
                       PRIORITY, STATUS, USER_EDITED, PRIOR_MAP_ID, CONDITION,
                       MIG_SQL, VERIFY_SQL, BATCH_CNT, ELAPSED_SECONDS, RETRY_COUNT,
                       CREATED_AT, UPD_TS
                FROM {map_table}
                WHERE MAP_ID = :1
                """,
                [map_id],
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "map_id": row[0],
            "map_type": self._mig__to_text(row[1]),
            "fr_table": self._mig__to_text(row[2]),
            "to_table": self._mig__to_text(row[3]),
            "use_yn": self._mig__to_text(row[4]),
            "trunc_yn": self._mig__to_text(row[5]),
            "priority": row[6],
            "status": self._mig__to_text(row[7]),
            "user_edited": self._mig__to_text(row[8]),
            "prior_map_id": row[9],
            "condition": self._mig__to_text(row[10]),
            "mig_sql": self._mig__to_text(row[11]),
            "verify_sql": self._mig__to_text(row[12]),
            "batch_cnt": row[13],
            "elapsed_seconds": row[14],
            "retry_count": row[15],
            "created_at": self._mig__to_text(row[16]),
            "upd_ts": self._mig__to_text(row[17]),
        }

    # NEXT_MIG_INFO_DTL에서 컬럼 매핑 목록을 조회한다.
    def _mig__load_details(self, map_id: int) -> list[dict[str, Any]]:
        detail_table = self._mig__qualify_table("NEXT_MIG_INFO_DTL", self.system_schema)
        with self._mig__connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT MAP_DTL, MAP_ID, FR_COL, TO_COL
                FROM {detail_table}
                WHERE MAP_ID = :1
                ORDER BY MAP_DTL ASC
                """,
                [map_id],
            )
            rows = cur.fetchall()
        return [
            {"map_dtl": r[0], "map_id": r[1], "fr_col": self._mig__to_text(r[2]), "to_col": self._mig__to_text(r[3])}
            for r in rows
        ]

    def _mig__check_dependencies(self, job: dict[str, Any]) -> dict[str, Any]:
        prior_map_id = job.get("prior_map_id")
        try:
            prior_map_id_int = int(prior_map_id) if prior_map_id is not None and str(prior_map_id).strip() else 0
        except (TypeError, ValueError):
            return {"ok": False, "status": "WAITING", "message": f"Invalid PRIOR_MAP_ID={prior_map_id}"}

        if prior_map_id_int > 0:
            prior = self._mig__load_job(prior_map_id_int)
            if not prior:
                return {"ok": False, "status": "WAITING", "message": f"Prior MAP_ID={prior_map_id} not found"}
            prior_status = str(prior.get("status") or "").upper()
            if prior_status != "PASS":
                return {
                    "ok": False,
                    "status": "WAITING",
                    "message": f"Prior MAP_ID={prior_map_id} status={prior_status or 'NULL'}",
                }

        target_dep = self._mig__check_same_target_priority_dependencies(job)
        if not target_dep["ok"]:
            return target_dep

        return {"ok": True, "message": "Dependencies passed"}

    def _mig__check_same_target_priority_dependencies(self, job: dict[str, Any]) -> dict[str, Any]:
        to_table = str(job.get("to_table") or "").strip()
        priority = job.get("priority")
        map_id = int(job.get("map_id") or 0)
        if not to_table or priority is None:
            return {"ok": True, "message": "No same-target priority dependency"}

        map_table = self._mig__qualify_table("NEXT_MIG_INFO", self.system_schema)
        with self._mig__connect() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    f"""
                    SELECT MAP_ID, STATUS
                    FROM {map_table}
                    WHERE DBMS_LOB.SUBSTR(TO_TABLE, 200, 1) = :1
                      AND PRIORITY < :2
                      AND MAP_ID != :3
                    ORDER BY PRIORITY DESC, MAP_ID DESC
                    """,
                    [to_table, priority, map_id],
                )
            except Exception:
                cur.execute(
                    f"""
                    SELECT MAP_ID, STATUS
                    FROM {map_table}
                    WHERE TO_TABLE = :1
                      AND PRIORITY < :2
                      AND MAP_ID != :3
                    ORDER BY PRIORITY DESC, MAP_ID DESC
                    """,
                    [to_table, priority, map_id],
                )
            rows = cur.fetchall()

        for prior_map_id, status in rows:
            prior_status = str(self._mig__to_text(status) or "").strip().upper()
            if prior_status != "PASS":
                return {
                    "ok": False,
                    "status": "WAITING",
                    "message": f"Same target prior MAP_ID={prior_map_id} status={prior_status or 'NULL'}",
                }
        return {"ok": True, "message": "Same-target priority dependencies passed"}

    def _mig__save_final_sql(self, map_id: int, mig_sql: str, verify_sql: str) -> None:
        assignments = []
        params: list[Any] = []
        clean_mig_sql = str(mig_sql or "").strip()
        clean_verify_sql = str(verify_sql or "").strip()
        if clean_mig_sql:
            params.append(clean_mig_sql)
            assignments.append(f"MIG_SQL = :{len(params)}")
        if clean_verify_sql:
            params.append(clean_verify_sql)
            assignments.append(f"VERIFY_SQL = :{len(params)}")
        if not assignments:
            return

        params.append(map_id)
        map_table = self._mig__qualify_table("NEXT_MIG_INFO", self.system_schema)
        with self._mig__connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {map_table}
                SET {", ".join(assignments)},
                    UPD_TS = CURRENT_TIMESTAMP
                WHERE MAP_ID = :{len(params)}
                """,
                params,
            )
            conn.commit()

    def _mig__summary_result(self, result: dict[str, Any]) -> dict[str, Any]:
        summary = {
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
        }
        for key in ["message", "error", "generation_source", "affected_rows", "elapsed_seconds", "retry_count"]:
            if key in result:
                summary[key] = result.get(key)
        return summary

    def _mig__truncate_target(self, job: dict[str, Any]) -> None:
        target = self._mig__qualify_table(job["to_table"], self.target_schema)
        with self._mig__connect() as conn:
            cur = conn.cursor()
            cur.execute(f"TRUNCATE TABLE {target}")
            conn.commit()

    # MIG_SQL script를 statement 단위로 실행하고 처리 row 수를 합산한다.
    def _mig__execute_sql_script(self, sql_script: str) -> int:
        statements = self._mig__split_sql_script(sql_script)
        total_rowcount = 0
        with self._mig__connect() as conn:
            cur = conn.cursor()
            for stmt in statements:
                cleaned = stmt.strip().rstrip(";")
                if cleaned:
                    cur.execute(cleaned)
                    if cur.rowcount and cur.rowcount > 0:
                        total_rowcount += cur.rowcount
            conn.commit()
        return total_rowcount

    # VERIFY_SQL 결과의 모든 값이 0인지 확인한다.
    def _mig__execute_verify_sql_with_rows(self, verify_sql: str) -> tuple[bool, str, list[dict[str, Any]]]:
        statements = self._mig__split_sql_script(verify_sql)
        if not statements:
            return False, "verify_sql is empty", []
        last_rows = []
        columns = []
        with self._mig__connect() as conn:
            cur = conn.cursor()
            for stmt in statements:
                cleaned = stmt.strip().rstrip(";")
                if not cleaned:
                    continue
                cur.execute(cleaned)
                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    last_rows = cur.fetchall()
        if not last_rows:
            return False, "Verification SQL returned no rows", []
        result_rows = [
            {str(columns[i] if i < len(columns) else i): self._mig__to_text(value) for i, value in enumerate(row)}
            for row in last_rows
        ]
        for row in last_rows:
            for value in row:
                text_value = self._mig__to_text(value).strip()
                if text_value == "":
                    return False, f"Mismatch found: {row}", result_rows
                try:
                    is_zero = Decimal(text_value) == Decimal("0")
                except (InvalidOperation, ValueError):
                    is_zero = text_value == "0"
                if not is_zero:
                    return False, f"Mismatch found: {row}", result_rows
        return True, "All Verification Passed", result_rows

    # 최종 상태, 소요시간, retry count, batch count를 NEXT_MIG_INFO에 저장한다.
    def _mig__update_job_status(self, map_id: int, status: str, elapsed_seconds: int, retry_count: int) -> None:
        map_table = self._mig__qualify_table("NEXT_MIG_INFO", self.system_schema)
        with self._mig__connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {map_table}
                SET STATUS = :1,
                    ELAPSED_SECONDS = :2,
                    RETRY_COUNT = :3,
                    BATCH_CNT = NVL(BATCH_CNT, 0) + 1,
                    UPD_TS = CURRENT_TIMESTAMP
                WHERE MAP_ID = :4
                """,
                [status, elapsed_seconds, retry_count, map_id],
            )
            conn.commit()

    # 마이그레이션 단계 로그를 NEXT_MIG_LOG에 저장한다.
    def _mig__write_log(
        self,
        map_id: int,
        log_type: str,
        log_level: str,
        step_name: str,
        status: str,
        message: str,
        retry_count: int = 0,
        generate_sql: str | None = None,
    ) -> None:
        log_table = self._mig__qualify_table("NEXT_MIG_LOG", self.system_schema)
        seq = self._mig__qualify_table("MIGRATION_LOG_SEQ", self.system_schema)
        safe_message = str(message or "")[:4000]
        try:
            with self._mig__connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    INSERT INTO {log_table}
                        (CREATED_AT, STATUS, MESSAGE, LOG_ID, MAP_ID, LOG_TYPE,
                         LOG_LEVEL, STEP_NAME, RETRY_COUNT, MIG_KIND, GENERATE_SQL)
                    VALUES
                        (CURRENT_TIMESTAMP, :1, :2, {seq}.NEXTVAL, :3, :4,
                         :5, :6, :7, 'DB_MIG', :8)
                    """,
                    [status, safe_message, map_id, log_type, log_level, step_name, retry_count, generate_sql],
                )
                conn.commit()
        except Exception:
            pass

    def _mig__split_sql_script(self, sql_script: str) -> list[str]:
        text = str(sql_script or "")
        statements: list[str] = []
        buffer: list[str] = []
        in_single = False
        in_double = False
        for ch in text:
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            if ch == ";" and not in_single and not in_double:
                statement = "".join(buffer).strip()
                if statement:
                    statements.append(statement)
                buffer = []
            else:
                buffer.append(ch)
        tail = "".join(buffer).strip()
        if tail:
            statements.append(tail)
        return statements

    def _mig__as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "y", "yes", "on"}

    def _mig__qualify_table(self, table_name: str, schema: str | None) -> str:
        clean = str(table_name or "").strip()
        clean_schema = str(schema or "").strip().upper()
        if not clean:
            raise ValueError("table_name is empty")
        if "." in clean or not clean_schema:
            return clean
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean_schema):
            raise ValueError(f"Invalid schema: {clean_schema}")
        return f"{clean_schema}.{clean}"

    def _mig__to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    # action="generate_to_sql": TO_SQL을 생성한다.
    def _sql__generate_to_sql(self, space_nm: Any, sql_id: Any, last_error: Any = None) -> dict[str, Any]:
        if not str(space_nm or "").strip() or not str(sql_id or "").strip():
            raise ValueError("space_nm and sql_id are required")
        job = self._sql__load_job(space_nm, sql_id)
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

        # EDIT_FR_SQL이 있으면 원본 FR_SQL보다 우선 사용한다.
        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "source SQL is empty"}

        mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._sql__build_mapping_schema_text(job)
        prompt = self._sql__render_to_sql_prompt(
            from_sql=source_sql,
            mapping_schema_text=mapping_schema_text,
            source_schema=str(self.source_schema or "").strip() or "UNKNOWN",
            target_schema=str(self.target_schema or "").strip() or "UNKNOWN",
            last_error=str(last_error or "None"),
        )
        to_sql = self._sql__sanitize_to_sql(self._sql__call_llm(prompt))

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

    # action="generate_bind_sql": BIND_SQL을 생성한다.
    def _sql__generate_bind_sql(self, space_nm: Any, sql_id: Any, to_sql: Any = None, last_error: Any = None) -> dict[str, Any]:
        job = self._sql__load_job(space_nm, sql_id)
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

        # EDIT_FR_SQL이 있으면 원본 FR_SQL보다 우선 사용한다.
        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": "FAIL-BIND", "error": "source SQL is empty"}

        mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._sql__build_mapping_schema_text(job)
        try:
            prompt = self._sql__build_bind_sql_prompt(job, final_to_sql, mapping_schema_text, last_error)
            bind_sql = self._sql__sanitize_to_sql(self._sql__call_llm(prompt))
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

    # action="generate_test_sql": TEST_SQL을 생성한다.
    def _sql__generate_test_sql(self, space_nm: Any, sql_id: Any, to_sql: Any = None, bind_sql: Any = None, bind_set: Any = None, last_error: Any = None) -> dict[str, Any]:
        job = self._sql__load_job(space_nm, sql_id)
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

        # EDIT_FR_SQL이 있으면 원본 FR_SQL보다 우선 사용한다.
        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": "FAIL-TEST", "error": "source SQL is empty"}

        mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._sql__build_mapping_schema_text(job)
        try:
            prompt = self._sql__build_test_sql_prompt(job, final_to_sql, final_bind_sql, final_bind_set, mapping_schema_text, last_error)
            test_sql = self._sql__sanitize_to_sql(self._sql__call_llm(prompt))
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

    # action="run_sql_conversion_job": TO_SQL, BIND_SQL, TEST_SQL 생성과 검증을 수행한다.
    def _sql_run_sql_conversion_job(self, sql_id: str, space_nm: str, command: dict[str, Any]) -> dict[str, Any]:

        if (sql_id is None or str(sql_id).strip() == "") or (space_nm is None or str(space_nm).strip() == ""):
            return {"ok": False, "error": "sql_id and space_nm are required for run_sql_conversion_job"}
        sql_id = str(sql_id or "").strip()
        space_nm = str(space_nm or "").strip()

        started = time.perf_counter()
        max_attempts = max(1, int(command.get("max_attempts") or 3))

        job = self._sql__load_job(space_nm, sql_id)
        if not job:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "job not found"}

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
            self._raise_if_batch_stop_requested()
            mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._sql__build_mapping_schema_text(job)
            last_failure: dict[str, Any] = {}
            to_sql_executed = False
            bind_sql_executed = False
            test_sql_executed = False
            for attempt in range(1, max_attempts + 1):
                self._raise_if_batch_stop_requested()
                retry_count = attempt - 1
                last_retry_count = retry_count
                job = self._sql__load_job(space_nm, sql_id) or job
                user_edited = str(job.get("user_edited") or "").strip().upper() == "Y"
                tag_kind = str(job.get("tag_kind") or "").strip().upper()

                if not to_sql_executed:
                    self._raise_if_batch_stop_requested()
                    if user_edited:
                        to_sql = str(job.get("to_sql") or "").strip()
                        if not to_sql:
                            raise ValueError("USER_EDITED=Y but TO_SQL is empty")
                        last_to_sql = to_sql
                        steps.append({"step": "generate_to_sql", "attempt": attempt, "status": "SUCCESS-TOBE", "message": "USER_EDITED=Y. Existing TO_SQL was used."})
                    else:
                        try:
                            to_sql_result = self._sql__generate_to_sql(space_nm, sql_id, last_error=last_failure.get("error", ""))
                            self._raise_if_batch_stop_requested()
                        except InterruptedError:
                            raise
                        except Exception as exc:
                            to_sql_result = {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": "FAIL-TOBE", "error": str(exc), "db_updated": False}
                        if to_sql_result.get("ok"):
                            to_sql_result["status"] = "SUCCESS-TOBE"
                        steps.append({"step": "generate_to_sql", "attempt": attempt, **self._sql__summary_result(to_sql_result)})
                        if not to_sql_result.get("ok"):
                            last_failure = {"status": "FAIL-TOBE", "error": to_sql_result.get("error") or "TO_SQL generation failed"}
                            self._sql__write_log(sql_id, space_nm, "TO_SQL", "FAIL", "GENERATE_TO_SQL", str(last_failure["error"])[:3900], retry_count, last_to_sql, int(time.perf_counter() - started), "TO_SQL_PROMPT")
                            if attempt < max_attempts:
                                continue
                            break
                        last_to_sql = str(to_sql_result.get("to_sql") or "").strip()
                    to_sql_executed = True
                    self._sql__write_log(sql_id, space_nm, "TO_SQL", "PASS", "GENERATE_TO_SQL", "TO_SQL generated", retry_count, last_to_sql, int(time.perf_counter() - started), "TO_SQL_PROMPT")

                if tag_kind != "SELECT":
                    self._raise_if_batch_stop_requested()
                    elapsed = int(time.perf_counter() - started)
                    self._sql__save_final_sql(sql_id, space_nm, last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
                    self._sql__update_job_status(sql_id, space_nm, "PASS-CONVERSION", elapsed, retry_count, status_tuning="READY")
                    self._sql__write_log(sql_id, space_nm, "TO_SQL", "PASS", "FINAL", "SQL Conversion completed without BIND/TEST because TAG_KIND is not SELECT", retry_count, last_to_sql, elapsed)
                    return {"ok": True, "space_nm": space_nm, "sql_id": sql_id, "status": "PASS-CONVERSION", "status_tuning": "READY", "elapsed_seconds": elapsed, "retry_count": retry_count, "steps": steps, "to_sql": last_to_sql, "map_ids": map_ids, "fr_tables": fr_tables, "rag_rule_count": rag_rule_count}

                if not bind_sql_executed:
                    try:
                        self._raise_if_batch_stop_requested()
                        bind_result = self._sql__generate_bind_sql(space_nm, sql_id, last_to_sql, last_failure.get("error", ""))
                        self._raise_if_batch_stop_requested()
                        steps.append({"step": "generate_bind_sql", "attempt": attempt, **self._sql__summary_result(bind_result)})
                        if not bind_result.get("ok"):
                            last_failure = {"status": "FAIL-BIND", "error": bind_result.get("error") or "BIND_SQL generation failed"}
                            self._sql__write_log(sql_id, space_nm, "BIND_SQL", "FAIL", "GENERATE_BIND_SQL", str(last_failure["error"])[:3900], retry_count, last_bind_sql, int(time.perf_counter() - started), "BIND_SQL_PROMPT")
                            if attempt < max_attempts:
                                continue
                            break
                        last_bind_sql = str(bind_result.get("bind_sql") or "").strip()
                        self._sql__write_log(sql_id, space_nm, "BIND_SQL", "PASS", "GENERATE_BIND_SQL", "BIND_SQL generated", retry_count, last_bind_sql, int(time.perf_counter() - started), "BIND_SQL_PROMPT")
                        existing_bind_set = str(job.get("bind_set") or "").strip()
                        if bind_result.get("status") == "BIND_SQL_SKIPPED_USER_EDITED" and existing_bind_set:
                            last_bind_set = existing_bind_set
                            bind_sql_executed = True
                            steps.append({"step": "execute_bind_sql", "attempt": attempt, "ok": True, "status": "BIND_SET_SKIPPED_USER_EDITED", "message": "USER_EDITED=Y. Existing BIND_SET was used."})
                        else:
                            clean_bind_sql = self._sql__prepare_runtime_sql(last_bind_sql, "EXECUTE_BIND_SQL")
                            if not clean_bind_sql:
                                raise ValueError("BIND_SQL is empty")
                            with self._sql__connect() as conn:
                                cur = conn.cursor()
                                cur.execute(clean_bind_sql)
                                columns = [desc[0] for desc in cur.description] if cur.description else []
                                rows = cur.fetchmany(20)
                            result_rows = [{str(columns[i] if i < len(columns) else i): self._sql__json_value(value) for i, value in enumerate(row)} for row in rows]
                            last_bind_set = json.dumps(result_rows, ensure_ascii=False)
                            bind_exec_result = {"ok": True, "status": "SUCCESS-BIND", "row_count": len(result_rows), "bind_set": last_bind_set}
                            steps.append({"step": "execute_bind_sql", "attempt": attempt, **self._sql__summary_result(bind_exec_result)})
                            bind_sql_executed = True
                            self._sql__write_log(sql_id, space_nm, "BIND_SET", "PASS", "EXECUTE_BIND_SQL", "BIND_SQL executed", retry_count, last_bind_set, int(time.perf_counter() - started))
                            self._raise_if_batch_stop_requested()
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        last_failure = {"status": "FAIL-BIND", "error": str(exc)}
                        steps.append({"step": "execute_bind_sql", "attempt": attempt, "ok": False, **last_failure})
                        self._sql__write_log(sql_id, space_nm, "BIND_SQL", "FAIL", "EXECUTE_BIND_SQL", str(exc)[:3900], retry_count, last_bind_sql, int(time.perf_counter() - started))
                        if attempt < max_attempts:
                            continue
                        break

                if not test_sql_executed:
                    try:
                        self._raise_if_batch_stop_requested()
                        test_result = self._sql__generate_test_sql(space_nm, sql_id, last_to_sql, last_bind_sql, last_bind_set, last_failure.get("error", ""))
                        self._raise_if_batch_stop_requested()
                        steps.append({"step": "generate_test_sql", "attempt": attempt, **self._sql__summary_result(test_result)})
                        if not test_result.get("ok"):
                            last_failure = {"status": "FAIL-TEST", "error": test_result.get("error") or "TEST_SQL generation failed"}
                            self._sql__write_log(sql_id, space_nm, "TEST_SQL", "FAIL", "GENERATE_TEST_SQL", str(last_failure["error"])[:3900], retry_count, last_test_sql, int(time.perf_counter() - started), "TEST_SQL_PROMPT")
                            if attempt < max_attempts:
                                continue
                            break
                        last_test_sql = str(test_result.get("test_sql") or "").strip()
                        self._sql__write_log(sql_id, space_nm, "TEST_SQL", "PASS", "GENERATE_TEST_SQL", "TEST_SQL generated", retry_count, last_test_sql, int(time.perf_counter() - started), "TEST_SQL_PROMPT")

                        clean_test_sql = self._sql__prepare_runtime_sql(last_test_sql, "EXECUTE_TEST_SQL")
                        if not clean_test_sql:
                            raise ValueError("TEST_SQL is empty")
                        with self._sql__connect() as conn:
                            cur = conn.cursor()
                            cur.execute(clean_test_sql)
                            columns = [desc[0] for desc in cur.description] if cur.description else []
                            rows = cur.fetchall()
                        result_rows = [{str(columns[i] if i < len(columns) else i): self._sql__json_value(value) for i, value in enumerate(row)} for row in rows]
                        self._raise_if_batch_stop_requested()
                        if not result_rows:
                            test_exec_result = {"ok": False, "status": "FAIL-TEST", "message": "TEST_SQL returned no rows", "result_rows": result_rows}
                        else:
                            sample_keys = {str(key).lower() for key in result_rows[0].keys()}
                            if not {"case_no", "from_count", "to_count"}.issubset(sample_keys):
                                test_exec_result = {"ok": False, "status": "FAIL-TEST", "message": f"TEST_SQL must return CASE_NO, FROM_COUNT, TO_COUNT. Actual columns: {sorted(sample_keys)}", "result_rows": result_rows}
                            else:
                                test_exec_result = {"ok": True, "status": "PASS-CONVERSION", "message": "All test counts matched", "result_rows": result_rows}
                                for row in result_rows:
                                    from_count = self._sql__get_row_value(row, "FROM_COUNT")
                                    to_count = self._sql__get_row_value(row, "TO_COUNT")
                                    if str(from_count).strip() != str(to_count).strip():
                                        test_exec_result = {"ok": False, "status": "FAIL-TEST", "message": f"Count mismatch: {row}", "result_rows": result_rows}
                                        break
                        steps.append({"step": "execute_test_sql", "attempt": attempt, **self._sql__summary_result(test_exec_result)})
                        if test_exec_result.get("ok"):
                            test_sql_executed = True
                            elapsed = int(time.perf_counter() - started)
                            self._sql__save_final_sql(sql_id, space_nm, last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
                            self._sql__update_job_status(sql_id, space_nm, "PASS-CONVERSION", elapsed, retry_count, status_tuning="READY")
                            self._sql__write_log(sql_id, space_nm, "TEST_SQL", "PASS", "EXECUTE_TEST_SQL", "SQL Conversion test passed", retry_count, last_test_sql, elapsed)
                            return {"ok": True, "space_nm": space_nm, "sql_id": sql_id, "status": "PASS-CONVERSION", "status_tuning": "READY", "elapsed_seconds": elapsed, "retry_count": retry_count, "steps": steps, "to_sql": last_to_sql, "bind_sql": last_bind_sql, "bind_set": last_bind_set, "test_sql": last_test_sql, "test_rows": test_exec_result.get("result_rows"), "map_ids": map_ids, "fr_tables": fr_tables, "rag_rule_count": rag_rule_count}
                        last_failure = {"status": "FAIL-TEST", "error": test_exec_result.get("message") or "TEST_SQL validation failed"}
                        self._sql__write_log(sql_id, space_nm, "TEST_SQL", "FAIL", "EXECUTE_TEST_SQL", str(last_failure["error"])[:3900], retry_count, last_test_sql, int(time.perf_counter() - started))
                        if attempt < max_attempts:
                            continue
                        break
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        last_failure = {"status": "FAIL-TEST", "error": str(exc)}
                        steps.append({"step": "execute_test_sql", "attempt": attempt, "ok": False, **last_failure})
                        self._sql__write_log(sql_id, space_nm, "TEST_SQL", "FAIL", "EXECUTE_TEST_SQL", str(exc)[:3900], retry_count, last_test_sql, int(time.perf_counter() - started))
                        if attempt < max_attempts:
                            continue
                        break

            final_status = str(last_failure.get("status") or self._sql__fallback_conversion_failure_status(last_to_sql, last_bind_sql, last_bind_set, last_test_sql))
            elapsed = int(time.perf_counter() - started)
            self._sql__save_final_sql(sql_id, space_nm, last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
            self._sql__update_job_status(sql_id, space_nm, final_status, elapsed, last_retry_count)
            self._sql__write_log(sql_id, space_nm, "ERROR", "FAIL", "FINAL", str(last_failure.get("error") or "Max attempts reached")[:3900], last_retry_count, last_test_sql or last_bind_sql or last_to_sql, elapsed)
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": final_status, "error": last_failure.get("error") or "Max attempts reached", "elapsed_seconds": elapsed, "retry_count": last_retry_count, "steps": steps}
        except InterruptedError as exc:
            elapsed = int(time.perf_counter() - started)
            self._sql__save_final_sql(sql_id, space_nm, last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
            self._sql__update_job_status(sql_id, space_nm, "STOPPED", elapsed, last_retry_count)
            self._sql__write_log(sql_id, space_nm, "ERROR", "WARN", "STOP_REQUESTED", str(exc)[:3900], last_retry_count, last_test_sql or last_bind_sql or last_to_sql, elapsed)
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": "STOPPED", "error": str(exc), "elapsed_seconds": elapsed, "retry_count": last_retry_count, "steps": steps}
        except Exception as exc:
            elapsed = int(time.perf_counter() - started)
            final_status = self._sql__fallback_conversion_failure_status(last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
            self._sql__save_final_sql(sql_id, space_nm, last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
            self._sql__update_job_status(sql_id, space_nm, final_status, elapsed, last_retry_count)
            self._sql__write_log(sql_id, space_nm, "ERROR", "FAIL", "RUN_FULL", str(exc)[:3900], last_retry_count, last_test_sql or last_bind_sql or last_to_sql, elapsed)
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": final_status, "error": str(exc), "elapsed_seconds": elapsed, "retry_count": last_retry_count, "steps": steps}

    # ======================================================================
    # 공통 코드
    # ======================================================================
    # DB 입력값으로 Oracle SQLAlchemy connection string을 만든다.
    def _sql__connection_string(self) -> str:
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

    # 같은 DB 접속 정보는 SQLDatabase 인스턴스를 캐시해 재사용한다.
    def _sql__get_db(self):
        self._sql__ensure_runtime_dependencies()
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
            self._db_cache[cache_key] = SQLDatabase.from_uri(self._sql__connection_string())
        self.db = self._db_cache[cache_key]
        return self.db

    # DB 연결에 필요한 런타임 패키지를 확인한다.
    def _sql__ensure_runtime_dependencies(self) -> None:
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
        if not AUTO_INSTALL_MISSING_PACKAGES:
            raise ModuleNotFoundError("Missing packages: " + ", ".join(missing_packages))
        for package in missing_packages:
            self._sql__pip_install(package)

    def _sql__pip_install(self, package: str) -> None:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

    def _sql__post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
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

    @contextmanager
    def _sql__connect(self):
        db = self._sql__get_db()
        with db._engine.connect() as conn:
            raw = conn.connection
            yield raw

    # NEXT_SQL_INFO에서 space_nm/sql_id에 해당하는 작업 row를 조회한다.
    def _sql__load_job(self, space_nm: Any, sql_id: Any) -> dict[str, Any] | None:
        table = self._sql__qualify_table("NEXT_SQL_INFO", self.system_schema)
        space_nm = str(space_nm or "").strip()
        sql_id = str(sql_id or "").strip()
        if not space_nm or not sql_id:
            raise ValueError("space_nm and sql_id are required")

        with self._sql__connect() as conn:
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
            "tag_kind": self._sql__to_text(row[0]),
            "space_nm": self._sql__to_text(row[1]),
            "sql_id": self._sql__to_text(row[2]),
            "fr_sql": self._sql__to_text(row[3]),
            "edit_fr_sql": self._sql__to_text(row[4]),
            "target_table": self._sql__to_text(row[5]),
            "to_sql": self._sql__to_text(row[6]),
            "status_conversion": self._sql__to_text(row[7]),
            "log": self._sql__to_text(row[8]),
            "tuned_fr_sql": self._sql__to_text(row[9]),
            "tuned_to_sql": self._sql__to_text(row[10]),
            "sql_length": self._sql__to_text(row[11]),
            "map_type": self._sql__to_text(row[12]),
            "priority": row[13],
            "batch_cnt": row[14],
            "upd_ts": self._sql__to_text(row[15]),
            "user_edited": self._sql__to_text(row[16]),
            "bind_sql": self._sql__to_text(row[17]),
            "bind_set": self._sql__to_text(row[18]),
            "test_sql": self._sql__to_text(row[19]),
            "status_tuning": self._sql__to_text(row[20]),
            "retry_count": row[21],
        }


    # TARGET_TABLE의 FR_TABLE 기준으로 mapping/RAG 정보를 구성한다.
    def _sql__build_mapping_schema_text(self, job: dict[str, Any]) -> tuple[str, list[int], list[str], int]:

        fr_tables = self._sql__extract_target_fr_tables(job.get("target_table"))
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

        normalized_fr_tables = {self._sql__normalize_table_name(name) for name in fr_tables if self._sql__normalize_table_name(name)}

        sections = ["[TARGET_TABLE_FR_TABLE_HINTS]"]
        for table_name in fr_tables:
            sections.append(f"  - {table_name}")

        sections.append("\n[MIGRATION_MAP_IDS]")
        map_ids: list[int] = []
        table = self._sql__qualify_table("NEXT_MIG_INFO", self.system_schema)
        detail = self._sql__qualify_table("NEXT_MIG_INFO_DTL", self.system_schema)
        with self._sql__connect() as conn:
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
            fr_table_text = self._sql__to_text(fr_table)
            normalized_fr_table = self._sql__normalize_table_name(fr_table_text)
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
            table_name for table_name in fr_tables if self._sql__normalize_table_name(table_name) not in matched_fr_tables
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
                    self._sql__to_text(v) for v in (map_type, fr_table, fr_col, to_table, to_col, condition)
                ]
                sections.append(
                    f"  - map_id={map_id}; map_type={map_type}; from={fr_table}.{fr_col or '*'}; to={to_table}.{to_col or '*'}; condition={condition}"
                )
        sections.append("\n[SQL_CONVERSION_RAG_GUIDANCE]")
        rag_lines = self._sql__load_conversion_rag_rules(fr_tables)
        sections.extend(rag_lines)
        rag_rule_count = len([line for line in rag_lines if line.strip().startswith("- {")])
        return "\n".join(sections), map_ids, fr_tables, rag_rule_count

    def _sql__load_conversion_rag_rules(self, fr_tables: list[str]) -> list[str]:
        table = self._sql__qualify_table("NEXT_MIG_RAG_INFO", self.system_schema)
        if not fr_tables:
            return ["  - No FR_TABLE hints for SQL_CONVERSION RAG lookup."]
        lines = []
        try:
            with self._sql__connect() as conn:
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
                                    "rule_type": self._sql__to_text(rule_type),
                                    "source_tables": self._sql__to_text(source_tables),
                                    "guidance": self._sql__to_text(guidance),
                                    "source_sql": self._sql__to_text(source_sql)[:1000],
                                    "target_sql": self._sql__to_text(target_sql)[:1000],
                                },
                                ensure_ascii=False,
                            )
                        )
        except Exception:
            return ["  - No SQL_CONVERSION RAG rules loaded."]
        return lines or ["  - No SQL_CONVERSION RAG rules found for FR_TABLE hints."]

    # to_sql_prompt placeholder를 실제 값으로 치환한다.
    def _sql__render_to_sql_prompt(
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

    # bind_sql_prompt placeholder를 실제 값으로 치환한다.
    def _sql__render_bind_sql_prompt(
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

    # test_sql_prompt placeholder를 실제 값으로 치환한다.
    def _sql__render_test_sql_prompt(
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

    def _sql__call_llm(self, prompt: str) -> str:
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
        data = self._sql__post_json(url, payload, {"Authorization": f"Bearer {api_key}"})
        return str(data["choices"][0]["message"].get("content", ""))

    def _sql__sanitize_to_sql(self, value: str) -> str:
        text = str(value or "").strip()
        if text.startswith("```"):
            fence = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.I | re.S)
            if fence:
                text = fence.group(1).strip()
        text = text.rstrip(";").strip()
        if not text:
            raise ValueError("LLM returned empty SQL")
        return text

    def _sql__build_bind_sql_prompt(self, job: dict[str, Any], to_sql: str, mapping_schema_text: str, last_error: Any = None) -> str:
        # EDIT_FR_SQL이 있으면 원본 FR_SQL보다 우선 사용한다.
        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            raise ValueError("source SQL is empty")

        fr_tables = self._sql__extract_target_fr_tables(job.get("target_table"))
        source_schema = str(self.source_schema or "").strip().upper()
        if source_schema:
            for table_name in fr_tables:
                clean_table = str(table_name or "").strip().strip('"')
                if not clean_table or "." in clean_table:
                    continue
                source_sql = re.sub(rf"(?<![A-Z0-9_$#.]){re.escape(clean_table)}(?![A-Z0-9_$#])", f"{source_schema}.{clean_table}", source_sql, flags=re.I)

        return self._sql__render_bind_sql_prompt(
            from_sql=source_sql,
            to_sql=to_sql,
            mapping_schema_text=mapping_schema_text,
            source_schema=source_schema or "UNKNOWN",
            target_schema=str(self.target_schema or "").strip() or "UNKNOWN",
            last_error=str(last_error or "None"),
        )

    def _sql__build_test_sql_prompt(self, job: dict[str, Any], to_sql: str, bind_sql: str, bind_set: str, mapping_schema_text: str, last_error: Any = None) -> str:
        # EDIT_FR_SQL이 있으면 원본 FR_SQL보다 우선 사용한다.
        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            raise ValueError("source SQL is empty")

        return self._sql__render_test_sql_prompt(
            from_sql=source_sql,
            to_sql=to_sql,
            bind_sql=bind_sql,
            bind_set=bind_set,
            mapping_schema_text=mapping_schema_text,
            source_schema=str(self.source_schema or "").strip() or "UNKNOWN",
            target_schema=str(self.target_schema or "").strip() or "UNKNOWN",
            last_error=str(last_error or "None"),
        )

    def _sql__save_final_sql(self, sql_id: str, space_nm: str, to_sql: str, bind_sql: str, bind_set: str, test_sql: str) -> None:
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
        table = self._sql__qualify_table("NEXT_SQL_INFO", self.system_schema)
        with self._sql__connect() as conn:
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

    # SQL Conversion 최종 상태와 batch count를 NEXT_SQL_INFO에 저장한다.
    def _sql__update_job_status(self, sql_id: str, space_nm: str, status_conversion: str, elapsed_seconds: int, retry_count: int, status_tuning: str | None = None) -> None:
        assignments = ["STATUS_CONVERSION = :1", "RETRY_COUNT = :2", "BATCH_CNT = NVL(BATCH_CNT, 0) + 1", "LOG = :3", "UPD_TS = CURRENT_TIMESTAMP"]
        params: list[Any] = [status_conversion, retry_count, f"STATUS_CONVERSION={status_conversion}; elapsed={elapsed_seconds}s; retry={retry_count}"]
        if status_tuning:
            params.append(status_tuning)
            assignments.append(f"STATUS_TUNING = :{len(params)}")
        params.extend([space_nm, sql_id])
        table = self._sql__qualify_table("NEXT_SQL_INFO", self.system_schema)
        with self._sql__connect() as conn:
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

    # SQL Conversion 단계 로그를 NEXT_SQL_LOG에 저장한다.
    def _sql__write_log(self, sql_id: str, space_nm: str, sql_kind: str, status: str, stage_name: str, message: str, retry_count: int = 0, sql_content: str | None = None, elapsed_seconds: int | None = None, prompt_name: str | None = None) -> None:
        table = self._sql__qualify_table("NEXT_SQL_LOG", self.system_schema)
        try:
            with self._sql__connect() as conn:
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

    def _sql__prepare_runtime_sql(self, sql_text: str, stage: str) -> str:
        clean_sql = self._sql__sanitize_to_sql(sql_text)
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

    def _sql__get_row_value(self, row: dict[str, Any], key: str) -> Any:
        if key in row:
            return row[key]
        lowered = key.lower()
        for existing_key, value in row.items():
            if str(existing_key).lower() == lowered:
                return value
        return None

    def _sql__json_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        if isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    def _sql__summary_result(self, result: dict[str, Any]) -> dict[str, Any]:
        summary = {"ok": bool(result.get("ok")), "status": result.get("status")}
        for key in ["message", "error", "row_count", "elapsed_seconds", "retry_count"]:
            if key in result:
                summary[key] = result.get(key)
        return summary

    def _sql__fallback_conversion_failure_status(self, to_sql: str, bind_sql: str, bind_set: str, test_sql: str) -> str:
        if not self._sql__to_text(to_sql).strip():
            return "FAIL-TOBE"
        if not self._sql__to_text(bind_sql).strip() or not self._sql__to_text(bind_set).strip():
            return "FAIL-BIND"
        return "FAIL-TEST"

    def _sql__extract_target_fr_tables(self, value: Any) -> list[str]:
        text = self._sql__to_text(value).strip()
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

    def _sql__normalize_table_name(self, value: Any) -> str:
        text = self._sql__to_text(value).strip().strip('"').upper()
        if "." in text:
            text = text.split(".")[-1]
        return text

    def _sql__qualify_table(self, table_name: str, schema: str | None) -> str:
        clean = str(table_name or "").strip()
        clean_schema = str(schema or "").strip().upper()
        if not clean:
            raise ValueError("table_name is empty")
        if "." in clean or not clean_schema:
            return clean
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean_schema):
            raise ValueError(f"Invalid schema: {clean_schema}")
        return f"{clean_schema}.{clean}"

    def _sql__to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "read"):
            value = value.read()
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    def _sql__as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "t", "y", "yes", "on"}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.upper() in {"1", "Y", "YES", "TRUE", "ON"}


def _read_text_env(name: str, file_name: str) -> str:
    direct_value = os.getenv(name)
    if direct_value:
        return direct_value
    file_path = _env(file_name)
    if not file_path:
        return ""
    return Path(file_path).read_text(encoding="utf-8")


def _load_service_config_file() -> dict[str, Any]:
    path = _env("SMARTMIGRATE_MONITOR_CONFIG")
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_service_config() -> dict[str, Any]:
    """Build background monitor config from env so no Langflow input is required."""
    file_config = _load_service_config_file()
    config = {
        "run_yn": _env("SMARTMIGRATE_RUN_YN", "Y"),
        "db_host": _env("SMARTMIGRATE_DB_HOST", str(DEFAULT_DB_CONFIG["db_host"])),
        "db_port": _env_int("SMARTMIGRATE_DB_PORT", int(DEFAULT_DB_CONFIG["db_port"])),
        "db_service_name": _env("SMARTMIGRATE_DB_SERVICE_NAME", str(DEFAULT_DB_CONFIG["db_service_name"])),
        "db_username": _env("SMARTMIGRATE_DB_USERNAME", str(DEFAULT_DB_CONFIG["db_username"])),
        "db_password": os.getenv("SMARTMIGRATE_DB_PASSWORD", str(DEFAULT_DB_CONFIG["db_password"])),
        "llm_base_url": _env("SMARTMIGRATE_LLM_BASE_URL", str(DEFAULT_LLM_CONFIG["llm_base_url"])),
        "llm_api_key": os.getenv("SMARTMIGRATE_LLM_API_KEY", str(DEFAULT_LLM_CONFIG["llm_api_key"])),
        "llm_model": _env("SMARTMIGRATE_LLM_MODEL", str(DEFAULT_LLM_CONFIG["llm_model"])),
        "llm_max_tokens": _env_int("SMARTMIGRATE_LLM_MAX_TOKENS", int(DEFAULT_LLM_CONFIG["llm_max_tokens"])),
        "llm_timeout_seconds": _env_int("SMARTMIGRATE_LLM_TIMEOUT_SECONDS", int(DEFAULT_LLM_CONFIG["llm_timeout_seconds"])),
        "chat_input": _env("SMARTMIGRATE_CHAT_INPUT", ""),
        "mig_sql_prompt": _read_text_env("SMARTMIGRATE_MIG_SQL_PROMPT", "SMARTMIGRATE_MIG_SQL_PROMPT_FILE"),
        "verify_sql_prompt": _read_text_env("SMARTMIGRATE_VERIFY_SQL_PROMPT", "SMARTMIGRATE_VERIFY_SQL_PROMPT_FILE"),
        "to_sql_prompt": _read_text_env("SMARTMIGRATE_TO_SQL_PROMPT", "SMARTMIGRATE_TO_SQL_PROMPT_FILE"),
        "bind_sql_prompt": _read_text_env("SMARTMIGRATE_BIND_SQL_PROMPT", "SMARTMIGRATE_BIND_SQL_PROMPT_FILE"),
        "test_sql_prompt": _read_text_env("SMARTMIGRATE_TEST_SQL_PROMPT", "SMARTMIGRATE_TEST_SQL_PROMPT_FILE"),
        "system_schema": str(DEFAULT_DB_CONFIG["system_schema"]),
        "source_schema": str(DEFAULT_DB_CONFIG["source_schema"]),
        "target_schema": str(DEFAULT_DB_CONFIG["target_schema"]),
        "migration_max_attempts": _env_int("SMARTMIGRATE_MIGRATION_MAX_ATTEMPTS", 3),
        "sql_conversion_max_attempts": _env_int("SMARTMIGRATE_SQL_CONVERSION_MAX_ATTEMPTS", 3),
        "no_job_sleep_seconds": 10,
        "error_sleep_seconds": _env_int("SMARTMIGRATE_ERROR_SLEEP_SECONDS", 60),
    }
    config.update(file_config)
    config["no_job_sleep_seconds"] = 10
    return config


def main() -> None:
    config = build_service_config()
    supervisor = object.__new__(BatchAgentCommandTool)
    supervisor._apply_config(config)
    supervisor._console(
        "main entered "
        f"run_yn={supervisor._run_yn_value(config)} "
        f"db={config.get('db_host')}:{config.get('db_port')}/{config.get('db_service_name')} "
        f"schema={config.get('system_schema')}"
    )

    if not supervisor._run_yn_equals_y(config):
        supervisor._write_batch_log_safe(
            config,
            None,
            0,
            "NOT_STARTED",
            message="Batch supervisor was not started because Run YN is not Y.",
        )
        return

    result = supervisor._start(config)
    supervisor._console(f"main finished status={result.get('status')} message={result.get('message')}")


if __name__ == "__main__":
    main()

