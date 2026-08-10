"""Shared runtime state and callbacks for supervisor tools."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from smart_migrate.integrations.llm.LlmFallback import reset_active_model

_stop_event = threading.Event()
PAUSE_FLAG = Path(__file__).resolve().parent.parent.parent / "runtime" / "agent.pause"
WAKE_FLAG = Path(__file__).resolve().parent.parent.parent / "runtime" / "agent.wake"
ACTIVE_JOB_FILE = Path(__file__).resolve().parent.parent.parent / "runtime" / "active_job.json"


def is_stop_requested() -> bool:
    return _stop_event.is_set()


def request_stop() -> None:
    _stop_event.set()


mig_registry: dict = {}
sql_registry: dict = {}
tuning_registry: dict = {}
formatting_registry: dict = {}

callbacks: dict = {}

# Kept in memory only for per-cycle execution guards and SQL log context.
cycle_metrics: dict = {}
batch_metrics: dict = {}
_job_execution_lock = threading.Lock()


def init_callbacks(**kwargs):
    for key, val in kwargs.items():
        callbacks[key] = val


def get_registries():
    return mig_registry, sql_registry, tuning_registry, formatting_registry


def refresh_jobs_after_tool() -> None:
    refresh_jobs = callbacks.get("refresh_jobs")
    if refresh_jobs:
        refresh_jobs()


def _job_value(job, *names: str):
    for name in names:
        if isinstance(job, dict) and name in job:
            return job.get(name)
        if hasattr(job, name):
            return getattr(job, name)
    return None


def sql_job_display_id(job, fallback: str | int = "") -> str:
    sql_id = _job_value(job, "SQL_ID", "sql_id")
    space_nm = _job_value(job, "SPACE_NM", "space_nm")
    if sql_id and space_nm:
        return f"{sql_id} / {space_nm}"
    return str(sql_id or space_nm or fallback or "")


def migration_job_display_id(job, fallback: str | int = "") -> str:
    map_id = _job_value(job, "MAP_ID", "map_id")
    return str(map_id or fallback or "")


def set_active_job(agent_name: str, job_id: str | int, stage: str | None = None) -> None:
    ACTIVE_JOB_FILE.parent.mkdir(exist_ok=True)
    payload = {
        "agent": str(agent_name or ""),
        "id": str(job_id or ""),
        "stage": str(stage or ""),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    ACTIVE_JOB_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def clear_active_job() -> None:
    ACTIVE_JOB_FILE.unlink(missing_ok=True)


def start_batch_metrics(batch_no: int) -> None:
    """Register a supervisor batch number for detailed SQL logs."""
    batch_metrics.clear()
    batch_metrics["batch_no"] = batch_no


def start_cycle_metrics(cycle_no: int) -> None:
    """Start an in-memory supervisor cycle state."""
    reset_active_model()
    cycle_metrics.clear()
    cycle_metrics.update(
        {
            "cycle_no": cycle_no,
            "batch_no": batch_metrics.get("batch_no", 0),
            "job_executed": False,
        }
    )


def mark_job_executed() -> None:
    if cycle_metrics:
        cycle_metrics["job_executed"] = True


def claim_job_execution() -> bool:
    """Return True only for the first job tool executed in the current cycle."""
    with _job_execution_lock:
        if bool(cycle_metrics.get("job_executed")):
            return False
        mark_job_executed()
        return True


def was_job_executed() -> bool:
    return bool(cycle_metrics.get("job_executed"))


def get_current_metric_context() -> dict[str, int | None]:
    """Return current supervisor batch/cycle numbers for detailed SQL logs."""
    if not cycle_metrics:
        return {"batch_no": None, "cycle_no": None}
    return {
        "batch_no": int(cycle_metrics.get("batch_no") or 0),
        "cycle_no": int(cycle_metrics.get("cycle_no") or 0),
    }


def finish_cycle_metrics(logger=None) -> None:
    """Clear the current in-memory supervisor cycle state."""
    if logger and cycle_metrics:
        logger.info(f"[SupervisorCycle] cycle {cycle_metrics.get('cycle_no')} finished")
    cycle_metrics.clear()
