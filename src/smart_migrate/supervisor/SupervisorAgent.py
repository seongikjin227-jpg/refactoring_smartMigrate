"""Supervisor Agent.

외부 while 루프가 사이클을 반복하고, 각 사이클마다 LangGraph 기반 ReAct 그래프를
한 번 invoke합니다. 수퍼바이저 LLM이 아래 도구를 순서에 따라 직접 선택·호출합니다.

  Tool 1: poll_jobs          — DB 폴링 및 레지스트리 갱신
  Tool 2: run_data_migration — 데이터 이관 (MigrationOrchestrator)
  Tool 3: run_sql_conversion — SQL 변환   (SqlConversionAgent)
  Tool 4: run_sql_tuning     — SQL 튜닝   (SqlTuningAgent)
  Tool 5: run_sql_formatting — SQL 포맷팅 (SqlFormattingAgent)
  Tool 6: request_wait        — 다음 사이클 전 대기
"""

import logging
import os
import signal
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from smart_migrate.supervisor.SupervisorGraph import build_supervisor_graph
from smart_migrate.supervisor.SupervisorPrompt import SUPERVISOR_SYSTEM_PROMPT
from smart_migrate.supervisor.SupervisorState import SupervisorState
from smart_migrate.config.AppSettings import SUPERVISOR_RECURSION_LIMIT
import smart_migrate.supervisor.SupervisorJobRegistry as supervisor_tools
from smart_migrate.supervisor.SupervisorJobRegistry import is_stop_requested, request_stop

logger = logging.getLogger("migration_agent")


class SupervisorAgent:
    """멀티 에이전트 시스템의 최상위 오케스트레이터."""

    def __init__(self) -> None:
        from smart_migrate.repositories.MigrationJobRepository import (
            get_pending_jobs as get_mig_jobs,
            increment_batch_count as mig_inc,
        )
        from smart_migrate.agents.db_migration.MigrationAgent import MigrationOrchestrator
        from smart_migrate.repositories.SqlJobRepository import (
            get_pending_jobs as get_sql_jobs,
            get_tuning_jobs as get_tuning_jobs_func,
            get_formatting_jobs as get_formatting_jobs_func,
            increment_batch_count as sql_inc,
        )
        from smart_migrate.agents.sql_conversion.SqlConversionAgent import SqlConversionAgent
        from smart_migrate.agents.sql_tuning.SqlTuningAgent import SqlTuningAgent
        from smart_migrate.agents.sql_formatting.SqlFormattingAgent import SqlFormattingAgent

        dm = MigrationOrchestrator()
        sql_conversion = SqlConversionAgent()
        sql_tuning = SqlTuningAgent()
        sql_formatting = SqlFormattingAgent()

        self._graph = build_supervisor_graph(
            get_migration_jobs=get_mig_jobs,
            get_sql_jobs=get_sql_jobs,
            get_tuning_jobs=get_tuning_jobs_func,
            get_formatting_jobs=get_formatting_jobs_func,
            mig_increment_batch=mig_inc,
            mig_process_job=dm.process_job,
            sql_increment_batch=sql_inc,
            sql_process_job=sql_conversion.process_job,
            tune_process_job=sql_tuning.process_job,
            format_process_job=sql_formatting.process_job,
            logger=logger,
        )

    def run(self) -> None:
        """SIGINT/SIGTERM 을 등록하고 Supervisor 사이클 루프를 실행한다."""
        logger.info("============================================================")
        logger.info(" Multi-Agent Supervisor 시작 (LLM ReAct Mode)")
        logger.info("  ├─ Tool 1: poll_jobs           — DB 폴링")
        logger.info("  ├─ Tool 2: run_data_migration  — 데이터 이관")
        logger.info("  ├─ Tool 3: run_sql_conversion  — SQL 변환")
        logger.info("  ├─ Tool 4: run_sql_tuning      — SQL 튜닝")
        logger.info("  ├─ Tool 5: run_sql_formatting  — SQL 포맷팅")
        logger.info("  └─ Tool 6: request_wait        — 사이클 대기")
        logger.info("============================================================")

        self._register_signal_handlers()
        batch_no = int(datetime.now().strftime("%Y%m%d%H%M%S"))
        supervisor_tools.start_batch_metrics(batch_no)
        logger.info(f"[Supervisor] Batch {batch_no} 시작")

        cycle = 0
        try:
            while not is_stop_requested():
                cycle += 1
                supervisor_tools.start_cycle_metrics(cycle)
                logger.info(f"\n{'=' * 50}")
                logger.info(f"[Supervisor] Cycle {cycle} 시작")

                human_content = (
                    f"사이클 {cycle}을 시작합니다. "
                    "poll_jobs()를 호출하여 현황을 파악하고 작업을 처리하세요."
                )

                initial_state: SupervisorState = {
                    "messages": [
                        SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT),
                        HumanMessage(content=human_content),
                    ],
                    "cycle": cycle,
                    "stop_requested": False,
                }

                try:
                    self._graph.invoke(
                        initial_state,
                        config={"recursion_limit": SUPERVISOR_RECURSION_LIMIT},
                    )
                except (KeyboardInterrupt, SystemExit):
                    break
                except Exception:
                    logger.exception(
                        "[Supervisor] 예기치 못한 오류로 사이클이 중단되었습니다."
                    )
                    raise
                finally:
                    # 그래프가 조기 종료되더라도 cycle 메모리 상태는 정리한다.
                    supervisor_tools.finish_cycle_metrics(logger)

        finally:
            logger.info("[Supervisor] 모든 에이전트가 종료되었습니다.")

    @staticmethod
    def _register_signal_handlers() -> None:
        signal_count = {"n": 0}

        def _handle(_signum, _frame):
            signal_count["n"] += 1
            request_stop()
            if signal_count["n"] == 1:
                try:
                    msg = "[Supervisor] Stop signal received. Finishing current job...\n"
                    os.write(2, msg.encode("utf-8", errors="ignore"))
                except OSError:
                    pass
                # Raise KeyboardInterrupt so blocking LLM/tool calls are interrupted immediately.
                raise KeyboardInterrupt
            else:
                os._exit(130)

        signal.signal(signal.SIGINT, _handle)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle)

