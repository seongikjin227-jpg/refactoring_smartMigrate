from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType02LlmClassifier(Component):
    display_name = "02 LLM Classifier"
    description = "Classifies user intent for routing. POC mode uses rules; production can replace this with an LLM prompt."
    name = "NewType02LlmClassifier"
    icon = "BrainCircuit"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        MessageTextInput(
            name="classifier_prompt",
            display_name="Classifier Prompt",
            required=False,
            info="Prompt contract for the future LLM classifier. POC does not call an LLM.",
        ),
        StrInput(
            name="classifier_mode",
            display_name="Classifier Mode",
            value="RULE_POC",
            required=False,
            info="RULE_POC for now. Use LLM later by wiring a model node before this router contract.",
        ),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="classify")]

    def classify(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            text = str(payload.get("user_request") or "").strip()
            classification = self._classify_by_rule(text)
            payload.update(
                {
                    "component": "02_llmClassifier",
                    "classification": classification,
                    "route": classification["route"],
                    "expected_latency": classification["expected_latency"],
                    "next_node": "03_intentRouter",
                }
            )
            payload.setdefault("history", []).append(
                {
                    "step": "llm_classifier",
                    "message": f"route={classification['route']}, latency={classification['expected_latency']}",
                }
            )
            self.status = payload
            return Data(data=payload)
        except Exception as exc:
            result = {"ok": False, "component": "02_llmClassifier", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _classify_by_rule(self, text: str) -> dict[str, Any]:
        lowered = text.lower()
        if re.search(r"(stop|중지|멈춰|정지|일시중지|pause|resume|재개)", lowered):
            return {
                "route": "STOP_CONTROL",
                "task_type": "CONTROL",
                "expected_latency": "FAST",
                "needs_pending_jobs": False,
                "needs_llm_answer": False,
                "reason": "supervisor control keyword",
            }
        if re.search(r"(status|상태|현황|요약|몇 건|카운트|dashboard|fail|실패)", lowered):
            return {
                "route": "FAST_STATUS",
                "task_type": "STATUS",
                "expected_latency": "FAST",
                "needs_pending_jobs": False,
                "needs_llm_answer": False,
                "reason": "status/dashboard keyword",
            }
        if re.search(r"(실행|run|start|처리|배치|pending|대기|job|작업|마이그레이션|migration|sql conversion|변환)", lowered):
            return {
                "route": "LONG_RUNNING_JOB",
                "task_type": "JOB_EXECUTION",
                "expected_latency": "LONG",
                "needs_pending_jobs": True,
                "needs_llm_answer": False,
                "reason": "job execution keyword",
            }
        return {
            "route": "GENERAL_CHAT",
            "task_type": "CHAT",
            "expected_latency": "FAST",
            "needs_pending_jobs": False,
            "needs_llm_answer": True,
            "reason": "general conversation",
        }

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("payload_json must be a JSON object")
        return parsed
