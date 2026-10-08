"""Check recovery guidance without executing any DB/LLM work."""
import unittest
from unittest.mock import Mock

from test_langflow_job_execution_components import Data, Intent, Remaining, Router as ExecutionRouter, load_component
from test_langflow_management_components import Data as ManagementData, Router as ManagementRouter


class GuidanceTests(unittest.TestCase):
    def test_management_failure_activates_only_exception_and_keeps_technical_error(self):
        router = ManagementRouter()
        router.payload_json = ManagementData({"user_request": "MAP_ID 59의 상태를 보여줘"})
        router._route_with_llm = Mock(side_effect=RuntimeError("LLM temporarily unavailable"))
        router.stop = Mock()
        self.assertEqual(router.management_agent_response().data, {})
        message = router.exception_response().text
        self.assertIn("현재 조회·변경 결과를 확인하지 못했습니다", message)
        self.assertIn("SQL_ID", message)
        self.assertIn("SPACE_NM", message)
        self.assertIn("Super Agent", message)
        self.assertIn("Code Interpreter Tool", message)
        self.assertIn("Correct TO_SQL", message)
        self.assertNotIn("LLM temporarily unavailable", message)
        self.assertEqual(router.status["error"], "LLM temporarily unavailable")
        router._route_with_llm.assert_called_once()
        router.stop.assert_called_once_with("management_agent")

    def test_short_management_exception_still_provides_complete_request_templates(self):
        router = ManagementRouter()
        router.payload_json = ManagementData({"user_request": "정리해줘"})
        router._route_with_llm = Mock(return_value={"management_route": "EXCEPTION", "exception_message": "대상이 모호합니다."})
        message = router.exception_response().text
        self.assertIn("대상이 모호합니다", message)
        self.assertIn("RAG Guide", message)
        self.assertIn("MAP_ID 59", message)
        self.assertIn("검토한 SQL 전문", message)

    def test_management_clarification_does_not_call_llm_or_change_route_to_agent(self):
        router = ManagementRouter()
        router.payload_json = ManagementData({"clarification_required": True, "clarification_message": "어느 작업인지 알려주세요.",
                                    "files": ["mapping.xlsx"]})
        router._route_with_llm = Mock()
        message = router.exception_response().text
        self.assertIn("어느 작업인지", message)
        self.assertIn("매핑", message)
        router._route_with_llm.assert_not_called()

    def test_intent_error_has_guidance_and_cannot_start_db_query(self):
        intent = Intent()
        intent._build_payload = Mock(side_effect=ValueError("invalid model response"))
        result = intent.job_execution_response().data
        self.assertFalse(result["should_execute"])
        self.assertTrue(result["final"])
        self.assertIn("상태 조회", result["answer_text"])
        self.assertIn("SQL_SEQ", result["answer_text"])
        remaining = Remaining()
        remaining.payload_json = Data(result)
        remaining._connect = Mock(side_effect=AssertionError("must not query"))
        remaining.get_remaining_jobs()
        remaining._connect.assert_not_called()

    def test_execution_lookup_failure_has_real_target_recovery_examples(self):
        router = ExecutionRouter()
        router.payload_json = Data({"ok": False, "error": "DB connection lost", "target_filter": {"map_ids": [731]}})
        message = router.no_runnable_response().text
        self.assertIn("MAP_ID 731", message)
        self.assertIn("연결", message)
        self.assertIn("현재 상태", message)
        self.assertNotIn("DB connection lost", message)
        self.assertFalse(router.status["should_execute"])
        self.assertFalse(router.status["run_all_pending"])
        self.assertIn("DB connection lost", router.status["error"])

    def test_no_runnable_reports_actual_status_and_does_not_promise_retry_success(self):
        router = ExecutionRouter()
        message = router._build_message_route_text({
            "job_route": "NO_RUNNABLE_JOB", "requested_domain": "MIG", "should_execute": True,
            "target_filter": {"map_ids": [731]}, "routing_reason": "실행 조건을 충족하지 않습니다.",
            "requested_target_status": {"migration": [{"map_id": 731, "status": "PASS", "use_yn": "Y", "retry_count": 0}]},
        })
        self.assertIn("STATUS=PASS", message)
        self.assertIn("MAP_ID 731", message)
        self.assertIn("RETRY_COUNT=0만으로", message)
        self.assertIn("최근 로그", message)

    def test_malformed_execution_payload_still_returns_help_message(self):
        router = ExecutionRouter()
        router.payload_json = "not JSON"
        message = router.no_runnable_response().text
        self.assertIn("실행을 준비하는 중", message)
        self.assertIn("SQL_SEQ", message)
        router.payload_json = Data({"target_filter": "not a filter"})
        message = router.no_runnable_response().text
        self.assertIn("요청 예시", message)

    def test_query_failures_explain_uncertainty_and_keep_raw_error_out_of_chat(self):
        cases = [("02_flow_management/04_dashboard.py", "run"),
                 ("02_flow_management/04_currentProgress.py", "run"),
                 ("03_job_execution/11_finalDashboard.py", "build_result"),
                 ("03_job_execution/11B_failureCauseAnalyzer.py", "build_analysis")]
        for path, method in cases:
            with self.subTest(path=path):
                component = load_component(path)()
                component._parse_payload = Mock(side_effect=RuntimeError("private connection diagnostic"))
                component.payload_json = Data({})
                message = getattr(component, method)().text
                self.assertFalse(component.status["ok"])
                self.assertEqual(component.status["error"], "private connection diagnostic")
                self.assertNotIn("private connection diagnostic", message)
                self.assertIn("운영자", message)
                self.assertIn("보여줘", message)


if __name__ == "__main__":
    unittest.main()
