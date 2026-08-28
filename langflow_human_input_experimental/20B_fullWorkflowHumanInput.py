from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from lfx.custom import Component
except Exception:
    from lfx.custom.custom_component.component import Component

from lfx.inputs.inputs import ActionPickerInput, BoolInput, DurationInput, HandleInput
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


HUMAN_INPUT_REQUIRED = "human_input_required"
_KIND_NODE_INPUT = "node_input"
_FALLBACK_ACTION = "fallback"
_UNIT_SECONDS = {"Seconds": 1, "Minutes": 60, "Hours": 3600, "Days": 86400}


def _action_id(label: str) -> str:
    return str(label).strip().lower().replace(" ", "_")


class NewType20BFullWorkflowHumanInput(Component):
    display_name = "20B Full Workflow Human Input"
    description = "Per-job HITL gate. Approve and Fallback pass the job item to the executor; Reject does not."
    icon = "HumanInput"
    name = "NewType20BFullWorkflowHumanInput"
    metadata = {"keywords": ["hitl", "human-in-the-loop", "decision", "full-workflow"]}

    inputs = [
        HandleInput(
            name="prompt_message",
            display_name="Input",
            input_types=["Message", "Data"],
            info="20A message shown to the human before this job is executed.",
            required=True,
        ),
        DataInput(
            name="job_item",
            display_name="Job Item",
            info="Original 18B loop item to release only after Approve or Fallback.",
            required=True,
            advanced=True,
        ),
        ActionPickerInput(
            name="decisions",
            display_name="User Choices",
            info="Approve continues. Reject returns a rejected payload. Timeout uses Fallback.",
            value=["Approve", "Reject"],
            real_time_refresh=True,
            required=True,
        ),
        DurationInput(
            name="timeout",
            display_name="Timeout",
            info="When fallback is enabled, unanswered requests route to Fallback after this window.",
            options=["Seconds", "Minutes", "Hours", "Days"],
            value={"value": 30, "unit": "Seconds"},
            advanced=True,
            show=True,
        ),
        BoolInput(
            name="enable_fallback",
            display_name="Enable Fallback",
            info="Add a fallback output that is treated as automatic approval.",
            value=True,
            advanced=True,
            real_time_refresh=True,
        ),
    ]

    outputs: list[Output] = [
        Output(display_name="Approve", name="branch_approve", method="route_branch", group_outputs=True, types=["Data"]),
        Output(display_name="Reject", name="branch_reject", method="route_branch", group_outputs=True, types=["Data"]),
        Output(display_name="Fallback", name="branch_fallback", method="route_branch", group_outputs=True, types=["Data"]),
    ]

    async def update_frontend_node(self, new_frontend_node: dict, current_frontend_node: dict) -> dict:
        new_frontend_node = await super().update_frontend_node(new_frontend_node, current_frontend_node)
        template = new_frontend_node.get("template", {})
        decisions = (template.get("decisions") or {}).get("value")
        self.update_outputs(new_frontend_node, "decisions", decisions if decisions is not None else [])
        self._sync_timeout_visibility(template)
        return new_frontend_node

    @staticmethod
    def _sync_timeout_visibility(template: dict) -> None:
        if "timeout" in template:
            template["timeout"]["show"] = bool((template.get("enable_fallback") or {}).get("value"))

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        if field_name == "enable_fallback" and "timeout" in build_config:
            build_config["timeout"]["show"] = bool(field_value)
        return build_config

    def update_outputs(self, frontend_node: dict, field_name: str, field_value: Any) -> dict:
        if field_name not in ("decisions", "enable_fallback"):
            return frontend_node

        template = frontend_node.get("template", {})

        def _other(field: str, attr_default: tuple[str, Any]) -> Any:
            if field in template:
                return (template.get(field) or {}).get("value")
            return getattr(self, attr_default[0], attr_default[1])

        actions = field_value if field_name == "decisions" else _other("decisions", ("decisions", []))
        fallback_on = field_value if field_name == "enable_fallback" else _other("enable_fallback", ("enable_fallback", True))

        outputs: list[Output] = []
        seen: set[str] = set()
        for label in actions or []:
            action_id = _action_id(label)
            if not action_id or action_id in seen:
                continue
            seen.add(action_id)
            outputs.append(
                Output(
                    display_name=str(label).strip(),
                    name=f"branch_{action_id}",
                    method="route_branch",
                    group_outputs=True,
                    types=["Data"],
                )
            )
        if fallback_on:
            outputs.append(
                Output(
                    display_name="Fallback",
                    name=f"branch_{_FALLBACK_ACTION}",
                    method="route_branch",
                    group_outputs=True,
                    types=["Data"],
                )
            )
        frontend_node["outputs"] = outputs
        return frontend_node

    def route_branch(self) -> Data:
        decision = self._injected_decision()
        if decision is None:
            if not self._has_downstream_consumer():
                self.status = {"component": "20B_fullWorkflowHumanInput", "status": "SKIPPED_NO_CONNECTED_OUTPUT"}
                return Data(data={})
            self._suspend()
            self._stop_all_branches()
            return Data(data={})

        chosen = str(decision.get("action_id") or "").strip().lower()
        for action_id in self._allowed_decisions():
            if action_id != chosen:
                self.stop(f"branch_{action_id}")

        if chosen == "reject":
            payload = self._rejected_payload(chosen)
        elif chosen in {"approve", _FALLBACK_ACTION}:
            payload = self._approved_payload(chosen)
        else:
            payload = self._rejected_payload(chosen)

        self.status = {
            "component": "20B_fullWorkflowHumanInput",
            "chosen_action": chosen,
            "hitl_status": payload.get("hitl_status"),
            "job_route": payload.get("planned_job_route") or payload.get("job_route"),
            "job_index": payload.get("job_index"),
            "total_jobs": payload.get("total_jobs"),
        }
        return Data(data=payload)

    def _approved_payload(self, chosen: str) -> dict[str, Any]:
        payload = self._payload()
        status = "APPROVED_BY_TIMEOUT" if chosen == _FALLBACK_ACTION else "APPROVED"
        payload.update(
            {
                "component": "20B_fullWorkflowHumanInput",
                "hitl_required": True,
                "hitl_status": status,
                "hitl_action": chosen,
                "hitl_approved": True,
                "hitl_rejected": False,
                "hitl_confirmed_at": datetime.now(timezone.utc).isoformat(),
                "next_node": "10C_migOneJobPocExecutor",
            }
        )
        payload.setdefault("history", []).append(
            {
                "step": "full_workflow_hitl",
                "message": f"action={chosen}, status={status}",
            }
        )
        return payload

    def _rejected_payload(self, chosen: str) -> dict[str, Any]:
        payload = self._payload()
        payload.update(
            {
                "component": "20B_fullWorkflowHumanInput",
                "hitl_required": True,
                "hitl_status": "REJECTED",
                "hitl_action": chosen,
                "hitl_approved": False,
                "hitl_rejected": True,
                "workflow_blocked": True,
                "skipped": True,
                "hitl_confirmed_at": datetime.now(timezone.utc).isoformat(),
                "answer_text": "작업 실행이 사용자 승인 단계에서 취소되었습니다.",
                "next_node": "manual_review",
            }
        )
        payload.setdefault("history", []).append(
            {
                "step": "full_workflow_hitl",
                "message": f"action={chosen}, status=REJECTED",
            }
        )
        return payload

    def _actions(self) -> list[tuple[str, str]]:
        seen: set[str] = set()
        actions: list[tuple[str, str]] = []
        for label in getattr(self, "decisions", []) or []:
            action_id = _action_id(label)
            if not action_id or action_id in seen:
                continue
            seen.add(action_id)
            actions.append((action_id, str(label).strip()))
        return actions

    def _rendered_prompt(self) -> str:
        prompt_message = getattr(self, "prompt_message", None)
        if isinstance(prompt_message, Message):
            return str(prompt_message.text or "")
        if isinstance(prompt_message, Data):
            data = prompt_message.data or {}
            for key in ("text", "message", "prompt", "prompt_text", "answer_text"):
                if data.get(key):
                    return str(data.get(key))
        return str(prompt_message or "")

    def _payload(self) -> dict[str, Any]:
        raw = getattr(self, "job_item", None)
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return {}

    def _timeout_seconds(self) -> int:
        timeout = getattr(self, "timeout", None) or {}
        if not isinstance(timeout, dict):
            return 0
        unit = timeout.get("unit", "Seconds") or "Seconds"
        try:
            amount = int(timeout.get("value", 0) or 0)
        except Exception:
            amount = 0
        return amount * _UNIT_SECONDS.get(unit, _UNIT_SECONDS["Seconds"])

    def _request_id(self) -> str:
        run_id = str(getattr(self.graph, "run_id", "") or "")
        return f"{self._id}:{run_id}"

    def _injected_decision(self) -> dict | None:
        decisions = getattr(self.graph, "human_input_decisions", None) if self.graph is not None else None
        if not isinstance(decisions, dict):
            return None
        return decisions.get(self._request_id())

    def _allowed_decisions(self) -> list[str]:
        ids = [action_id for action_id, _ in self._actions()]
        if getattr(self, "enable_fallback", True):
            ids.append(_FALLBACK_ACTION)
        return ids

    def _pause_request(self) -> dict[str, Any]:
        return {
            "request_id": self._request_id(),
            "kind": _KIND_NODE_INPUT,
            "prompt": self._rendered_prompt(),
            "options": [{"action_id": action_id, "label": label} for action_id, label in self._actions()],
            "allowed_decisions": self._allowed_decisions(),
            "timeout_seconds": self._timeout_seconds(),
            "fallback_action": _FALLBACK_ACTION if getattr(self, "enable_fallback", True) else None,
            "paused_at": datetime.now(timezone.utc).isoformat(),
        }

    def _has_downstream_consumer(self) -> bool:
        graph = getattr(self, "graph", None)
        successor_map = getattr(graph, "successor_map", None)
        if not isinstance(successor_map, dict):
            return True
        return bool(successor_map.get(self._id))

    def _suspend(self) -> None:
        self.graph.request_pause(reason=HUMAN_INPUT_REQUIRED, data=self._pause_request())
        self.status = {"component": "20B_fullWorkflowHumanInput", "status": "AWAITING_HUMAN_INPUT"}

    def _stop_all_branches(self) -> None:
        for action_id in self._allowed_decisions():
            self.stop(f"branch_{action_id}")
