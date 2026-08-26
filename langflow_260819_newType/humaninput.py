from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from typing import Any

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

try:
    from lfx.io import MultilineInput, Output
except Exception:  # pragma: no cover - older Langflow packages
    from lfx.io import MessageTextInput as MultilineInput
    from lfx.io import Output

from lfx.io import IntInput, StrInput
from lfx.schema.message import Message

try:
    from lfx.inputs.inputs import ActionPickerInput, BoolInput, DurationInput
except Exception:  # pragma: no cover - older Langflow packages
    ActionPickerInput = None
    DurationInput = None
    try:
        from lfx.io import BoolInput
    except Exception:  # pragma: no cover
        BoolInput = StrInput


HUMAN_INPUT_REQUIRED = "human_input_required"
_KIND_NODE_INPUT = "node_input"
_FALLBACK_ACTION = "fallback"
_UNIT_SECONDS = {"Minutes": 60, "Hours": 3600, "Days": 86400}


def _action_id(label: str) -> str:
    return str(label).strip().lower().replace(" ", "_")


def _decisions_input():
    if ActionPickerInput is not None:
        return ActionPickerInput(
            name="decisions",
            display_name="User Choices",
            info="Choices the human can pick; each becomes a branch output.",
            value=["Approve", "Reject"],
            real_time_refresh=True,
            required=True,
        )
    return StrInput(
        name="decisions",
        display_name="User Choices",
        value="Approve,Reject",
        required=True,
        info="Comma-separated choices. Example: Approve,Reject",
    )


def _timeout_input():
    if DurationInput is not None:
        return DurationInput(
            name="timeout",
            display_name="Timeout",
            info="A response received after this window is routed to the fallback branch when enabled.",
            options=["Minutes", "Hours", "Days"],
            value={"value": 10, "unit": "Minutes"},
            advanced=True,
            show=True,
        )
    return IntInput(
        name="timeout",
        display_name="Timeout Seconds",
        value=600,
        required=False,
        advanced=True,
        info="Seconds before fallback is selected when enabled.",
    )


def _prompt_input():
    kwargs = {
        "name": "prompt",
        "display_name": "Input",
        "info": "Content shown to the human for review.",
        "value": "",
        "required": True,
    }
    try:
        return MultilineInput(**kwargs)
    except TypeError:
        kwargs.pop("required", None)
        return MultilineInput(**kwargs)


def _fallback_enabled_input():
    kwargs = {
        "name": "enable_fallback",
        "display_name": "Enable Fallback",
        "info": "Add a fallback output used when the answer arrives after the timeout window.",
        "value": True,
        "advanced": True,
        "real_time_refresh": True,
    }
    try:
        return BoolInput(**kwargs)
    except TypeError:
        kwargs.pop("real_time_refresh", None)
        try:
            return BoolInput(**kwargs)
        except TypeError:
            kwargs["value"] = "true"
            return StrInput(**kwargs)


class SmartMigrateHumanInput(Component):
    display_name = "Human Input"
    description = "Pause the flow for a human-in-the-loop decision and route on the selected action."
    icon = "HumanInput"
    name = "HumanInput"
    metadata = {"keywords": ["hitl", "human-in-the-loop", "decision", "approval"]}

    inputs = [
        _prompt_input(),
        _decisions_input(),
        _fallback_enabled_input(),
        _timeout_input(),
    ]

    # Include fallback by default because SmartMigrate wires timeout as automatic
    # approval. Dynamic output refresh below keeps this compatible with the
    # official component behavior when the frontend supports it.
    outputs: list[Output] = [
        Output(display_name="Approve", name="branch_approve", method="route_branch", group_outputs=True),
        Output(display_name="Reject", name="branch_reject", method="route_branch", group_outputs=True),
        Output(display_name="Fallback", name="branch_fallback", method="route_branch", group_outputs=True),
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

        def _other(field: str, default: Any) -> Any:
            if field in template:
                return (template.get(field) or {}).get("value")
            return getattr(self, field, default)

        actions = field_value if field_name == "decisions" else _other("decisions", [])
        fallback_on = field_value if field_name == "enable_fallback" else _other("enable_fallback", True)

        outputs: list[Output] = []
        seen: set[str] = set()
        for label in self._labels(actions):
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
                )
            )

        if fallback_on:
            outputs.append(
                Output(
                    display_name="Fallback",
                    name=f"branch_{_FALLBACK_ACTION}",
                    method="route_branch",
                    group_outputs=True,
                )
            )

        frontend_node["outputs"] = outputs
        return frontend_node

    def route_branch(self) -> Message:
        decision = self._injected_decision()
        if decision is None:
            if not self._has_downstream_consumer():
                self.status = "Skipped: no connected outputs"
                return Message(text=self._rendered_prompt())
            self._suspend()
            return Message(text="")

        chosen = str(decision.get("action_id") or "").strip()
        for action_id in self._allowed_decisions():
            if action_id != chosen:
                self.stop(f"branch_{action_id}")
        self.status = {"chosen_action": chosen}
        return Message(text=self._rendered_prompt())

    def _actions(self) -> list[tuple[str, str]]:
        seen: set[str] = set()
        actions: list[tuple[str, str]] = []
        for label in self._labels(getattr(self, "decisions", [])):
            action_id = _action_id(label)
            if not action_id or action_id in seen:
                continue
            seen.add(action_id)
            actions.append((action_id, str(label).strip()))
        return actions

    def _labels(self, raw: Any) -> list[str]:
        if isinstance(raw, list):
            labels = [str(item).strip() for item in raw if str(item).strip()]
        else:
            labels = [item.strip() for item in str(raw or "Approve,Reject").split(",") if item.strip()]
        return labels or ["Approve", "Reject"]

    def _rendered_prompt(self) -> str:
        return str(getattr(self, "prompt", "") or "")

    def _timeout_seconds(self) -> int:
        timeout = getattr(self, "timeout", None) or {}
        if isinstance(timeout, dict):
            unit = timeout.get("unit", "Minutes") or "Minutes"
            return int(timeout.get("value", 0) or 0) * _UNIT_SECONDS.get(unit, 60)
        try:
            return max(0, int(timeout or 0))
        except Exception:
            return 0

    def _request_id(self) -> str:
        graph = getattr(self, "graph", None)
        run_id = str(getattr(graph, "run_id", "") or "")
        node_id = str(getattr(self, "_id", "") or self.name)
        return f"{node_id}:{run_id}"

    def _injected_decision(self) -> dict | None:
        graph = getattr(self, "graph", None)
        decisions = getattr(graph, "human_input_decisions", None) if graph is not None else None
        if not isinstance(decisions, dict):
            return None
        decision = decisions.get(self._request_id())
        return decision if isinstance(decision, dict) else None

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
        return bool(successor_map.get(getattr(self, "_id", "")))

    def _suspend(self) -> None:
        graph = getattr(self, "graph", None)
        request_pause = getattr(graph, "request_pause", None)
        if not callable(request_pause):
            raise RuntimeError(
                "This Langflow runtime does not expose graph.request_pause. "
                "Human Input requires Langflow suspend/resume support."
            )
        request_pause(reason=HUMAN_INPUT_REQUIRED, data=self._pause_request())
        self.status = "Awaiting human input"
