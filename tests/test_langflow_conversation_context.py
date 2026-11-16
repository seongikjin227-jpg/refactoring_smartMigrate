"""Check session memory boundaries and context handoff without a running Langflow."""
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch

from test_langflow_job_execution_components import Intent, Message
from test_langflow_management_components import Data, Router


class ChatMessage:
    def __init__(self, content):
        self.content = content


class ConversationContextTests(unittest.TestCase):
    def setUp(self):
        self.intent = Intent()
        self.message = Message("102번 진행해줘")
        self.intent.input_message = self.message
        self.memory = types.ModuleType("lfx.memory")
        self.memory.get_messages = Mock(return_value=[])
        self.messages_module = types.ModuleType("langchain_core.messages")
        self.messages_module.HumanMessage = self.messages_module.SystemMessage = ChatMessage

    def test_builtin_memory_uses_session_and_flow_and_latest_user_turns(self):
        self.intent.graph = types.SimpleNamespace(flow_id="flow-1")
        self.memory.get_messages.return_value = [
            {"sender": "User", "text": "102번 진행해줘", "session_id": "job-session"},
            {"sender": "User", "text": "102번 마이그레이션 하고 싶은데 어떻게 해?", "session_id": "job-session"},
        ]
        with patch.dict(sys.modules, {"lfx.memory": self.memory}):
            turns = self.intent._recent_user_requests(self.message, {}, self.message.text)
        self.assertEqual(turns, ["102번 마이그레이션 하고 싶은데 어떻게 해?"])
        self.memory.get_messages.assert_called_once_with(
            sender="User", session_id="job-session", flow_id="flow-1", limit=11, order="DESC")

    def test_no_session_never_queries_unscoped_memory(self):
        self.message.session_id = None
        with patch.dict(sys.modules, {"lfx.memory": self.memory}):
            self.assertEqual(self.intent._recent_user_requests(self.message, {}, self.message.text), [])
        self.memory.get_messages.assert_not_called()
        self.assertEqual(self.intent._chat_history_status, "NO_SESSION")

    def test_supplied_history_filters_roles_sessions_contexts_and_limits(self):
        self.message.context_id = "context-1"
        self.message.data = {"chat_history": [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "SQL Conversion"},
            {"sender": "User", "text": "foreign", "session_id": "another-session"},
            {"role": "user", "content": "foreign context", "context_id": "context-2"},
            {"role": "user", "content": "마이그레이션 102번 어떻게 해?"},
            {"role": "user", "content": "102번 진행해줘"},
        ]}
        self.intent.history_limit = 1
        turns = self.intent._recent_user_requests(self.message, {}, self.message.text)
        self.assertEqual(turns, ["마이그레이션 102번 어떻게 해?"])

    def test_workflow_trace_is_not_chat_history_and_lookup_failure_is_safe(self):
        self.message.data = {"history": [{"step": "router", "message": "MIG 102"}]}
        self.memory.get_messages.side_effect = RuntimeError("memory offline")
        with patch.dict(sys.modules, {"lfx.memory": self.memory}):
            self.assertEqual(self.intent._recent_user_requests(self.message, {}, self.message.text), [])
        self.assertEqual(self.intent._chat_history_status, "UNAVAILABLE")

    def test_resolved_request_and_history_reach_execution_payload(self):
        history = ["102번 마이그레이션 하고 싶은데 어떻게 해?"]
        self.message.data = {"recent_user_requests": history}
        self.intent.llm = Mock()
        self.intent.llm.invoke.return_value = ChatMessage(json.dumps({
            "route": "JOB_EXECUTION", "request_action": "EXECUTE", "requested_domain": "MIG",
            "execution_scope": "targeted", "target_filter": {"map_ids": [102]},
            "resolved_user_request": "마이그레이션 MAP_ID 102번을 실행해줘", "is_follow_up": True,
        }))
        with patch.dict(sys.modules, {"langchain_core.messages": self.messages_module}):
            payload = self.intent._build_payload()
        sent = json.loads(self.intent.llm.invoke.call_args.args[0][1].content)
        self.assertEqual(sent["recent_user_requests"], history)
        self.assertEqual(payload["recent_user_requests"], history)
        self.assertEqual(payload["user_request"], "102번 진행해줘")
        self.assertEqual(payload["resolved_user_request"], "마이그레이션 MAP_ID 102번을 실행해줘")
        self.assertEqual(payload["target_filter"]["map_ids"], [102])
        self.assertTrue(payload["is_follow_up"])
        self.assertTrue(payload["should_execute"])

    def test_ambiguous_number_cannot_execute_without_domain(self):
        result = self.intent._intent_from_response(json.dumps({
            "route": "JOB_EXECUTION", "request_action": "EXECUTE", "requested_domain": "UNKNOWN",
            "execution_scope": "targeted", "target_filter": {}, "clarification_required": False,
        }))
        self.assertTrue(result["clarification_required"])

    def test_04_receives_history_and_preserves_contextual_management_request(self):
        router = Router()
        history = ["102번 SQL Conversion 상태를 알고 싶어"]
        router.payload_json = Data({"user_request": "102번 상태 보여줘", "recent_user_requests": history})
        router.llm = Mock()
        router.llm.invoke.return_value = ChatMessage(json.dumps({
            "management_route": "MANAGEMENT_AGENT", "requested_domain": "SQL_CONVERSION",
            "resolved_user_request": "SQL Conversion SQL_SEQ 102번 상태를 보여줘",
        }))
        with patch.dict(sys.modules, {"langchain_core.messages": self.messages_module}):
            payload = router._get_routed_payload()
        sent = json.loads(router.llm.invoke.call_args.args[0][1].content)
        self.assertEqual(sent["recent_user_requests"], history)
        self.assertEqual(payload["requested_domain"], "SQL_CONVERSION")
        self.assertEqual(payload["effective_user_request"], "SQL Conversion SQL_SEQ 102번 상태를 보여줘")

    def test_04_does_not_bypass_upstream_ambiguity(self):
        router = Router()
        router.payload_json = Data({"clarification_required": True, "recent_user_requests": ["MIG 102", "Conversion 102"]})
        router.llm = Mock()
        self.assertEqual(router._get_routed_payload()["management_route"], "EXCEPTION")
        router.llm.invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
