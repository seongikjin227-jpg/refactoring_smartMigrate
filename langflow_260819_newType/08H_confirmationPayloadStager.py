from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

from lfx.io import MessageTextInput, Output, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


DEFAULT_STATE_DIR = ".smartmigrate_confirmation_state"


def _load_component_base():
    for module_name in (
        "langflow.custom.custom_component.base_component",
        "langflow.custom.custom_component.component",
        "lfx.custom.custom_component.component",
        "lfx.custom",
    ):
        try:
            module = import_module(module_name)
            component = getattr(module, "Component", None)
            if component is not None:
                return component
        except Exception:
            continue
    raise ImportError("Could not import Langflow Component base class")


Component = _load_component_base()


class NewType08HConfirmationPayloadStager(Component):
    display_name = "08H Confirmation Payload Stager"
    description = "Stores the execution payload before Human Input and returns only the approval prompt."
    name = "NewType08HConfirmationPayloadStager"
    icon = "ShieldQuestion"

    inputs = [
        DataInput(name="payload_json", display_name="Execution Payload", required=True),
        MessageTextInput(name="plan_message", display_name="Execution Plan Message", required=False),
        StrInput(name="state_dir", display_name="State Directory", value=DEFAULT_STATE_DIR, required=False, advanced=True),
    ]

    outputs = [
        Output(display_name="Prompt", name="prompt", method="build_prompt", types=["Message"]),
    ]

    def build_prompt(self) -> Message:
        payload = self._parse_payload(getattr(self, "payload_json", ""))
        confirmation_id = self._confirmation_id(payload)
        plan_text = self._message_text(getattr(self, "plan_message", ""))
        if not plan_text:
            plan_text = self._fallback_plan_text(payload)

        record = {
            "confirmation_id": confirmation_id,
            "status": "PENDING",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                **payload,
                "confirmation_id": confirmation_id,
                "confirmation_required": True,
                "confirmation_status": "PENDING",
            },
            "plan_message": plan_text,
        }
        path = self._record_path(confirmation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, default=str, indent=2), encoding="utf-8")

        prompt = self._prompt_text(confirmation_id, plan_text)
        self.status = {
            "component": "08H_confirmationPayloadStager",
            "confirmation_id": confirmation_id,
            "state_path": str(path),
            "status": "PENDING",
        }
        return Message(text=prompt)

    def _prompt_text(self, confirmation_id: str, plan_text: str) -> str:
        return "\n".join(
            [
                "요청하신 작업 계획입니다.",
                "",
                plan_text.strip(),
                "",
                f"confirmation_id={confirmation_id}",
                "",
                "진행 여부를 선택해주세요.",
                "- Approve: 작업을 시작합니다.",
                "- Reject: 작업을 취소합니다.",
                "- Timeout/Fallback: 자동 승인으로 처리합니다.",
            ]
        )

    def _fallback_plan_text(self, payload: dict[str, Any]) -> str:
        route = str(payload.get("job_route") or payload.get("planned_job_route") or "UNKNOWN")
        run_mode = str(payload.get("run_mode") or "all_pending")
        jobs = payload.get("selected_jobs")
        count = len(jobs) if isinstance(jobs, list) else payload.get("planned_job_count", 0)
        return "\n".join(
            [
                f"작업 유형: {route}",
                f"실행 모드: {run_mode}",
                f"실행 예정 건수: {count}",
            ]
        )

    def _confirmation_id(self, payload: dict[str, Any]) -> str:
        existing = str(payload.get("confirmation_id") or "").strip()
        if existing:
            return existing
        seed = json.dumps(
            {
                "job_route": payload.get("job_route"),
                "run_mode": payload.get("run_mode"),
                "target_filter": payload.get("target_filter"),
                "selected_jobs": payload.get("selected_jobs"),
                "user_request": payload.get("user_request") or payload.get("original_request"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        return f"CONF-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{digest}"

    def _record_path(self, confirmation_id: str) -> Path:
        state_dir = Path(str(getattr(self, "state_dir", None) or DEFAULT_STATE_DIR))
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", confirmation_id)
        return state_dir / f"{safe_id}.json"

    def _message_text(self, raw: Any) -> str:
        if isinstance(raw, Message):
            return str(raw.text or "")
        return str(raw or "")

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
