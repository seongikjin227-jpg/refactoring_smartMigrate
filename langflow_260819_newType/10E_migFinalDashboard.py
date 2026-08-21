from __future__ import annotations

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.message import Message


class NewType10EMigFinalDashboard(Component):
    display_name = "10E MIG Done Message"
    description = "Shows a simple completion message after the MIG loop is done."
    name = "NewType10EMigFinalDashboard"
    icon = "ClipboardCheck"

    inputs = [MessageTextInput(name="loop_result", display_name="Loop Result", required=False)]
    outputs = [Output(display_name="Result Message", name="result", method="build_result", types=["Message"])]

    def build_result(self) -> Message:
        payload = {
            "component": "10E_migFinalDashboard",
            "final": True,
            "message": "요청하신 작업이 완료됐습니다.",
        }
        self.status = payload
        return Message(text="요청하신 작업이 완료됐습니다.")
