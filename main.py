"""SmartMigrate background agent bootstrap.

Run:
  python main.py
"""

from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
ENV_PATH = ROOT_DIR / ".env"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)
else:
    print(f"ERROR: {ENV_PATH} file was not found. Stop startup.")
    sys.exit(1)

from smart_migrate.supervisor.SupervisorAgent import SupervisorAgent

_PID_FILE = ROOT_DIR / "runtime" / "agent.pid"
_PAUSE_FILE = ROOT_DIR / "runtime" / "agent.pause"


def startup_check() -> None:
    """Check DB, LLM, and table connectivity before running the agent."""
    print("\n[Startup] Checking connection status...")
    try:
        from scripts.init_db import check_llm_connection, check_oracle_connection, check_tables

        checks = [
            check_oracle_connection(),
            check_llm_connection(),
            *check_tables(),
        ]

        all_ok = True
        for result in checks:
            icon = "OK" if result.ok else "FAIL"
            print(f"  [{icon}] {result.name:<35} {result.detail}")
            if not result.ok:
                all_ok = False

        if not all_ok:
            print("\nERROR: Startup checks failed. Check .env settings.")
            sys.exit(1)
        print("SUCCESS: All connection checks passed.\n")
    except Exception as exc:
        print(f"\nERROR: Unexpected startup check error: {exc}")
        sys.exit(1)


def _write_pid() -> None:
    _PID_FILE.parent.mkdir(exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _cleanup() -> None:
    _PID_FILE.unlink(missing_ok=True)
    _PAUSE_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    startup_check()
    _write_pid()
    atexit.register(_cleanup)
    try:
        SupervisorAgent().run()
    finally:
        _cleanup()
