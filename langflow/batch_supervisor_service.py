from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from components.batch_agent_command_tool import BatchAgentCommandTool


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


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


def _load_config_file() -> dict[str, Any]:
    path = _env("SMARTMIGRATE_BATCH_CONFIG")
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_config() -> dict[str, Any]:
    file_config = _load_config_file()
    config = {
        "db_host": _env("SMARTMIGRATE_DB_HOST"),
        "db_port": _env_int("SMARTMIGRATE_DB_PORT", 1521),
        "db_service_name": _env("SMARTMIGRATE_DB_SERVICE_NAME"),
        "db_username": _env("SMARTMIGRATE_DB_USERNAME"),
        "db_password": os.getenv("SMARTMIGRATE_DB_PASSWORD", ""),
        "llm_base_url": _env("SMARTMIGRATE_LLM_BASE_URL"),
        "llm_api_key": os.getenv("SMARTMIGRATE_LLM_API_KEY", ""),
        "llm_model": _env("SMARTMIGRATE_LLM_MODEL", "claude-haiku-4-5-20251001"),
        "llm_max_tokens": _env_int("SMARTMIGRATE_LLM_MAX_TOKENS", 4096),
        "llm_timeout_seconds": _env_int("SMARTMIGRATE_LLM_TIMEOUT_SECONDS", 900),
        "mig_sql_prompt": _read_text_env("SMARTMIGRATE_MIG_SQL_PROMPT", "SMARTMIGRATE_MIG_SQL_PROMPT_FILE"),
        "verify_sql_prompt": _read_text_env("SMARTMIGRATE_VERIFY_SQL_PROMPT", "SMARTMIGRATE_VERIFY_SQL_PROMPT_FILE"),
        "to_sql_prompt": _read_text_env("SMARTMIGRATE_TO_SQL_PROMPT", "SMARTMIGRATE_TO_SQL_PROMPT_FILE"),
        "bind_sql_prompt": _read_text_env("SMARTMIGRATE_BIND_SQL_PROMPT", "SMARTMIGRATE_BIND_SQL_PROMPT_FILE"),
        "test_sql_prompt": _read_text_env("SMARTMIGRATE_TEST_SQL_PROMPT", "SMARTMIGRATE_TEST_SQL_PROMPT_FILE"),
        "system_schema": _env("SMARTMIGRATE_SYSTEM_SCHEMA"),
        "source_schema": _env("SMARTMIGRATE_SOURCE_SCHEMA"),
        "target_schema": _env("SMARTMIGRATE_TARGET_SCHEMA"),
        "migration_max_attempts": _env_int("SMARTMIGRATE_MIGRATION_MAX_ATTEMPTS", 3),
        "sql_conversion_max_attempts": _env_int("SMARTMIGRATE_SQL_CONVERSION_MAX_ATTEMPTS", 3),
        "no_job_sleep_seconds": _env_int("SMARTMIGRATE_NO_JOB_SLEEP_SECONDS", 600),
        "error_sleep_seconds": _env_int("SMARTMIGRATE_ERROR_SLEEP_SECONDS", 60),
        "status_log_alive_grace_seconds": _env_int("SMARTMIGRATE_STATUS_ALIVE_GRACE_SECONDS", 1800),
        "auto_install_packages": _env_bool("SMARTMIGRATE_AUTO_INSTALL_PACKAGES", False),
    }
    config.update(file_config)
    return config


def main() -> None:
    config = build_config()
    auto_start = _env_bool("SMARTMIGRATE_BATCH_AUTO_START", True)
    idle_sleep_seconds = _env_int("SMARTMIGRATE_BATCH_IDLE_SLEEP_SECONDS", 10)
    BatchAgentCommandTool.serve_forever(
        config,
        auto_start_on_boot=auto_start,
        idle_sleep_seconds=idle_sleep_seconds,
    )


if __name__ == "__main__":
    main()
