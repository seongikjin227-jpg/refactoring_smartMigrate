"""Supervisor cycle pacing tool."""

from __future__ import annotations

import os
import time

from langchain_core.tools import tool

from smart_migrate.supervisor.SupervisorJobRegistry import (
    PAUSE_FLAG,
    WAKE_FLAG,
    _stop_event,
    callbacks,
    was_job_executed,
)

_WAIT_STEP = 0.2
_PAUSE_STEP = 0.5


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


_MAX_WAIT_SECONDS = _env_int("SUPERVISOR_MAX_WAIT_SECONDS", 300)
_JOB_WAIT_SECONDS = _env_int("SUPERVISOR_JOB_WAIT_SECONDS", 1)
_IDLE_WAIT_SECONDS = _env_int("SUPERVISOR_IDLE_WAIT_SECONDS", 30)


@tool
def request_wait(seconds: int) -> str:
    """Wait before the next supervisor cycle."""
    logger = callbacks.get("logger")
    requested_seconds = int(seconds)
    configured_seconds = _JOB_WAIT_SECONDS if was_job_executed() else _IDLE_WAIT_SECONDS
    seconds = max(1, min(configured_seconds or requested_seconds, _MAX_WAIT_SECONDS))
    if logger and seconds != requested_seconds:
        logger.info(
            f"[request_wait] requested={requested_seconds}s, configured={seconds}s "
            f"(job_executed={was_job_executed()})"
        )

    paused_logged = False
    while PAUSE_FLAG.exists():
        if _stop_event.is_set():
            return "Stop requested. Pause wait interrupted."
        if not paused_logged:
            if logger:
                logger.info("[request_wait] paused by runtime/agent.pause")
            paused_logged = True
        time.sleep(_PAUSE_STEP)
    if paused_logged and logger:
        logger.info("[request_wait] pause released")

    elapsed = 0.0
    while elapsed < seconds:
        if _stop_event.is_set():
            return f"Stop requested. Wait interrupted after {elapsed:.1f}s."
        if PAUSE_FLAG.exists():
            break
        if WAKE_FLAG.exists():
            WAKE_FLAG.unlink(missing_ok=True)
            if logger:
                logger.info("[request_wait] wake signal received")
            return "Wake signal received. Starting next cycle."
        time.sleep(_WAIT_STEP)
        elapsed += _WAIT_STEP

    return f"Waited {seconds}s."
