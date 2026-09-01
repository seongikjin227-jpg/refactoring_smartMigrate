from __future__ import annotations

import logging
import json
import re
import time
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DropdownInput, FloatInput, MessageTextInput, Output, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput



def _workflow_log(step_name: str, status: str, message: str, log_level: str = "INFO") -> None:
    logging.getLogger("smartmigrate.workflow").log(logging.ERROR if str(log_level).upper() == "ERROR" else logging.INFO, str(message or ""), extra={"workflow_log": [0, "WORKFLOW", "LOOP_TEST_02_PROGRESS_OUTPUT_ADAPTER", str(log_level or "INFO").upper(), str(step_name or "")[:50], str(status or "")[:20], str(message or "")[:4000], 0]})

class LoopOutputTest02ProgressOutputAdapter(Component):
    display_name = "Loop Output Test 02 Progress Output Adapter"
    description = "Accumulates per-iteration loop messages so the final Chat Output can contain all progress."
    name = "LoopOutputTest02ProgressOutputAdapter"
    icon = "MessagesSquare"

    inputs = [
        DataInput(name="payload", display_name="18D Message Or Payload", required=True),
        DataInput(name="loop_result_input", display_name="18D Loop Result", required=False),
        DropdownInput(
            name="emit_mode",
            display_name="Emit Mode",
            options=["ACCUMULATED", "CURRENT_ONLY", "ACCUMULATED_WITH_FINAL_RESET"],
            value="ACCUMULATED",
            required=False,
        ),
        FloatInput(name="sleep_seconds", display_name="Sleep Seconds", value=0.0, required=False),
        StrInput(name="run_key", display_name="Run Key Override", required=False),
        BoolInput(name="reset_on_final", display_name="Reset On Final", value=True, required=False),
    ]

    outputs = [
        Output(display_name="Message", name="message", method="build_message", types=["Message"]),
        Output(display_name="Loop Result", name="loop_result", method="build_loop_result", types=["Data"]),
    ]

    def build_message(self) -> Message:
        result = self._build()
        self.status = result
        return Message(text=result["answer_text"])

    def build_loop_result(self) -> Data:
        result = self._build()
        self.status = result
        return Data(data=result["loop_result"])

    def _build(self) -> dict[str, Any]:
        _workflow_log("_BUILD", "START", "before _build")
        cached = getattr(self, "_cached_payload", None)
        if cached is not None:
            return cached

        payload = self._parse_any(getattr(self, "payload", ""))
        loop_result = self._parse_any(getattr(self, "loop_result_input", "")) if getattr(self, "loop_result_input", None) not in (None, "") else {}
        current_text = self._message_text(payload)
        current_result = loop_result or self._loop_result(payload)
        final = bool(payload.get("final") or current_result.get("loop_done") or current_result.get("final"))

        sleep_seconds = self._sleep_seconds()
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        run_key = self._run_key(payload, current_result)
        history_key = f"{self._id}_progress_history_{run_key}"
        history = list(self.ctx.get(history_key, []))
        if current_text:
            history.append(
                {
                    "seq": len(history) + 1,
                    "final": final,
                    "job_index": current_result.get("job_index") or payload.get("job_index"),
                    "total_jobs": current_result.get("total_jobs") or payload.get("total_jobs"),
                    "status": current_result.get("status") or payload.get("status"),
                    "text": current_text,
                }
            )

        emit_mode = str(getattr(self, "emit_mode", "") or "ACCUMULATED").upper()
        answer_text = current_text
        if emit_mode in {"ACCUMULATED", "ACCUMULATED_WITH_FINAL_RESET"}:
            answer_text = self._render_history(history)
        elif emit_mode == "CURRENT_ONLY":
            answer_text = current_text

        out = {
            **payload,
            "component": "LoopOutputTest02ProgressOutputAdapter",
            "answer_text": answer_text,
            "progress_run_key": run_key,
            "progress_event_count": len(history),
            "progress_emit_mode": emit_mode,
            "progress_sleep_seconds": sleep_seconds,
            "loop_result": {
                **current_result,
                "progress_run_key": run_key,
                "progress_event_count": len(history),
            },
        }
        self.update_ctx({history_key: history})
        if final and (bool(getattr(self, "reset_on_final", True)) or emit_mode == "ACCUMULATED_WITH_FINAL_RESET"):
            self.update_ctx({history_key: []})
        self._cached_payload = out
        _workflow_log("_BUILD", "END", "after _build")
        return out

    def _render_history(self, history: list[dict[str, Any]]) -> str:
        if not history:
            return ""
        lines = ["# Workflow Progress", ""]
        for item in history:
            seq = int(item.get("seq") or 0)
            status = str(item.get("status") or "-")
            index = item.get("job_index")
            total = item.get("total_jobs")
            suffix = f" ({index}/{total})" if index and total else ""
            lines.extend([f"## {seq}. {status}{suffix}", "", str(item.get("text") or "").strip(), ""])
        return "\n".join(lines).strip()

    def _message_text(self, payload: dict[str, Any]) -> str:
        for key in ("answer_text", "text", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if payload:
            return json.dumps(payload, ensure_ascii=False, default=str, indent=2)
        return ""

    def _loop_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = payload.get("loop_result")
        if isinstance(value, dict):
            return dict(value)
        return dict(payload)

    def _run_key(self, payload: dict[str, Any], loop_result: dict[str, Any]) -> str:
        override = str(getattr(self, "run_key", "") or "").strip()
        if override:
            return self._safe_key(override)
        for key in ("workflow_run_id", "run_id", "batch_id", "session_id", "chat_id"):
            value = payload.get(key) or loop_result.get(key)
            if str(value or "").strip():
                return self._safe_key(value)
        return "default"

    def _parse_any(self, raw: Any) -> dict[str, Any]:
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
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"text": text}

    def _safe_key(self, value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)
        return text[:120] or "default"

    def _sleep_seconds(self) -> float:
        try:
            return max(0.0, min(30.0, float(getattr(self, "sleep_seconds", 0.0) or 0.0)))
        except (TypeError, ValueError):
            return 0.0
