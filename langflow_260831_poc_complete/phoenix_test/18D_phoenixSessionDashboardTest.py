from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import nullcontext
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class PhoenixTest18DSessionDashboard(Component):
    display_name = "18D Phoenix Session Dashboard Test"
    description = "Writes one manual Phoenix/OpenInference session span containing only the 18D dashboard message, then passes it through."
    name = "PhoenixTest18DSessionDashboard"
    icon = "Activity"

    inputs = [
        DataInput(name="dashboard_message", display_name="18D Dashboard Message", required=True),
        DataInput(name="loop_result", display_name="18D Loop Result", required=False),
        StrInput(name="session_id", display_name="Session ID", required=False),
        StrInput(name="span_name", display_name="Span Name", value="smartmigrate.dashboard_message", required=False),
        StrInput(name="phoenix_project_name", display_name="Phoenix Project Name", required=False),
    ]

    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
        Output(display_name="Trace Result", name="trace_result", method="build_trace_result", types=["Data"]),
    ]

    def build_message(self) -> Message:
        payload = self._build()
        return Message(text=str(payload.get("message_text") or ""))

    def build_trace_result(self) -> Data:
        return Data(data=self._build())

    def _build(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached

        started = time.perf_counter()
        message_text = self._extract_message_text(getattr(self, "dashboard_message", ""))
        loop_result = self._parse_payload(getattr(self, "loop_result", None))
        session_id = str(
            getattr(self, "session_id", "")
            or loop_result.get("session_id")
            or loop_result.get("workflow_run_id")
            or loop_result.get("run_id")
            or uuid.uuid4()
        ).strip()
        span_name = str(getattr(self, "span_name", "") or "smartmigrate.dashboard_message").strip()
        project_name = str(getattr(self, "phoenix_project_name", "") or os.getenv("PHOENIX_PROJECT_NAME") or "").strip()
        if project_name:
            os.environ.setdefault("PHOENIX_PROJECT_NAME", project_name)

        trace_result = self._write_session_span(
            session_id=session_id,
            span_name=span_name,
            message_text=message_text,
            loop_result=loop_result,
        )
        result = {
            "ok": trace_result.get("ok", False),
            "component": "phoenix_test_18D_session_dashboard",
            "session_id": session_id,
            "span_name": span_name,
            "phoenix_project_name": project_name,
            "message_text": message_text,
            "message_length": len(message_text),
            "trace": trace_result,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        self.status = result
        self._cached_payload = result
        return result

    def _write_session_span(
        self,
        session_id: str,
        span_name: str,
        message_text: str,
        loop_result: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            from opentelemetry import trace

            span_attributes, using_session = self._openinference_session_helpers()
            tracer = trace.get_tracer("smartmigrate.phoenix_test")
            attributes = {
                self._attr(span_attributes, "OPENINFERENCE_SPAN_KIND", "openinference.span.kind"): "chain",
                self._attr(span_attributes, "SESSION_ID", "session.id"): session_id,
                self._attr(span_attributes, "INPUT_VALUE", "input.value"): "",
                self._attr(span_attributes, "OUTPUT_VALUE", "output.value"): message_text,
                "smartmigrate.dashboard_only": True,
                "smartmigrate.source_component": "18D_fullWorkflowDashboard",
            }
            for key in (
                "workflow_run_id",
                "run_id",
                "batch_id",
                "job_route",
                "planned_job_route",
                "job_index",
                "total_jobs",
                "status",
                "final",
                "loop_done",
            ):
                if key in loop_result and loop_result[key] not in (None, ""):
                    attributes[f"smartmigrate.{key}"] = str(loop_result[key])

            session_context = using_session(session_id) if using_session else nullcontext()
            with session_context:
                with tracer.start_as_current_span(span_name, attributes=attributes) as span:
                    span.set_attribute("smartmigrate.message_sha256", self._sha256(message_text))
                    span.set_attribute("smartmigrate.message_length", len(message_text))
            return {"ok": True, "session_id": session_id}
        except Exception as exc:
            return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}

    def _openinference_session_helpers(self) -> tuple[Any, Any]:
        try:
            from phoenix.otel import SpanAttributes, using_session

            return SpanAttributes, using_session
        except Exception:
            pass
        try:
            from openinference.instrumentation import using_session
        except Exception:
            using_session = None
        try:
            from openinference.semconv.trace import SpanAttributes
        except Exception:
            SpanAttributes = None
        return SpanAttributes, using_session

    def _attr(self, span_attributes: Any, name: str, fallback: str) -> str:
        if span_attributes is not None and hasattr(span_attributes, name):
            return str(getattr(span_attributes, name))
        return fallback

    def _extract_message_text(self, raw: Any) -> str:
        if isinstance(raw, Message):
            return str(raw.text or "")
        if isinstance(raw, Data):
            data = dict(raw.data or {})
            for key in ("answer_text", "message", "text", "output", "result"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return json.dumps(data, ensure_ascii=False, default=str)
        if isinstance(raw, dict):
            for key in ("answer_text", "message", "text", "output", "result"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return json.dumps(raw, ensure_ascii=False, default=str)
        return str(raw or "")

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, Message):
            raw = raw.text
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if not text:
            return {}
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except Exception:
            return {"text": text}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def _sha256(self, value: str) -> str:
        import hashlib

        return hashlib.sha256(value.encode("utf-8")).hexdigest()

