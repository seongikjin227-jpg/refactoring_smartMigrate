from __future__ import annotations

import re
from datetime import datetime, timezone
from importlib import import_module
from typing import Any

from lfx.inputs.inputs import ActionPickerInput, BoolInput, DurationInput, HandleInput
from lfx.io import DataInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


HUMAN_INPUT_REQUIRED = "human_input_required"
_KIND_NODE_INPUT = "node_input"
_FALLBACK_ACTION = "fallback"
_UNIT_SECONDS = {"Minutes": 60, "Hours": 3600, "Days": 86400}


def _load_component_base():
    for module_name in (
        "lfx.custom",
        "lfx.custom.custom_component.component",
        "langflow.custom.custom_component.base_component",
        "langflow.custom.custom_component.component",
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


def _action_id(label: str) -> str:
    return str(label).strip().lower().replace(" ", "_")


class NewType20HumanInput(Component):
    display_name = "20 Human Input"
    description = "Pause the flow for a human decision and emit payload only on the selected branch."
    icon = "HumanInput"
    name = "NewType20HumanInput"
    metadata = {"keywords": ["hitl", "human-in-the-loop", "decision", "approval"]}

    inputs = [
        HandleInput(
            name="prompt_message",
            display_name="Input",
            input_types=["Message", "Data"],
            info="Content shown to the human for review before they choose Approve, Reject, or Fallback.",
            required=True,
        ),
        DataInput(
            name="execution_data",
            display_name="Execution Data",
            info="Data to release only after Approve or Fallback.",
            required=False,
            advanced=True,
        ),
        ActionPickerInput(
            name="decisions",
            display_name="User Choices",
            info="Choices the human can pick; each becomes a branch output.",
            value=["Approve", "Reject"],
            real_time_refresh=True,
            required=True,
        ),
        DurationInput(
            name="timeout",
            display_name="Timeout",
            info="A response received after this window is routed to fallback when enabled.",
            options=["Minutes", "Hours", "Days"],
            value={"value": 10, "unit": "Minutes"},
            advanced=True,
            show=True,
        ),
        BoolInput(
            name="enable_fallback",
            display_name="Enable Fallback",
            info="Add a fallback output taken when no user action is answered in time.",
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
        fallback_on = (
            field_value if field_name == "enable_fallback" else _other("enable_fallback", ("enable_fallback", True))
        )

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
                self.status = "Skipped: no connected outputs"
                return Data(data={})
            self._suspend()
            return Data(data={})

        chosen = str(decision.get("action_id") or "")
        for action_id in self._allowed_decisions():
            if action_id != chosen:
                self.stop(f"branch_{action_id}")

        payload = self._payload()
        status = self._confirmation_status(chosen)
        confirmation_id = str(payload.get("confirmation_id") or self._extract_confirmation_id()).strip()
        if confirmation_id:
            payload["confirmation_id"] = confirmation_id
        payload.update(
            {
                "confirmation_required": True,
                "confirmation_status": status,
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
                "human_input_action": chosen,
                "component": "20_humanInput",
            }
        )
        payload.setdefault("history", []).append(
            {
                "step": "human_input_confirmation",
                "message": f"action={chosen}, status={status}",
            }
        )

        self.status = {
            "component": "20_humanInput",
            "chosen_action": chosen,
            "confirmation_status": status,
            "confirmation_id": confirmation_id,
        }
        return Data(data=payload)

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
            for key in ("text", "message", "prompt"):
                if data.get(key):
                    return str(data.get(key))
        if isinstance(prompt_message, str) and prompt_message.strip():
            return prompt_message

        return ""

    def _payload(self) -> dict[str, Any]:
        raw = getattr(self, "execution_data", None)
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        if raw in (None, ""):
            return {}
        raise ValueError("execution_data must be a Langflow Data object")

    def _confirmation_status(self, chosen: str) -> str:
        if chosen == "approve":
            return "APPROVED"
        if chosen == _FALLBACK_ACTION:
            return "APPROVED_BY_TIMEOUT"
        if chosen == "reject":
            return "REJECTED"
        return chosen.upper()

    def _extract_confirmation_id(self) -> str:
        match = re.search(r"\bconfirmation_id\s*=\s*([A-Za-z0-9_.:-]+)", self._rendered_prompt())
        return match.group(1).strip() if match else ""

    def _timeout_seconds(self) -> int:
        timeout = getattr(self, "timeout", None) or {}
        if not isinstance(timeout, dict):
            return 0
        unit = timeout.get("unit", "Minutes") or "Minutes"
        try:
            amount = int(timeout.get("value", 0) or 0)
        except Exception:
            amount = 0
        return amount * _UNIT_SECONDS.get(unit, _UNIT_SECONDS["Minutes"])

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
        self.status = "Awaiting human input"
