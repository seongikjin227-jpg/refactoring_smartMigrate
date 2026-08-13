# Supervisor Control Tool

Tool name: supervisor_control_tool

Description:
Control and inspect the Batch Supervisor (NEXT_BATCH_CONTROL). Use this tool when the user asks to start, stop, pause, resume supervisor or to inspect control status.

System prompt (for LLM assistant selecting this tool):
"""
Select `supervisor_control_tool` when the user explicitly requests supervisor control actions such as start/stop/pause/resume or asks for control status.

Safety rules:
- Starting/stopping the supervisor affects running processes; require confirmation for start/stop.
- Pause/resume are lighter-weight but also should be confirmed in sensitive environments.

Supported actions:
- `start`: Set NEXT_BATCH_CONTROL to RUNNING and optionally wake supervisor.
- `stop`: Set NEXT_BATCH_CONTROL to STOP_REQUESTED.
- `pause`: Set control to a paused state (if supported) or request stop.
- `resume`: Clear STOP_REQUESTED and set RUNNING.
- `status`: Return current NEXT_BATCH_CONTROL row and heartbeat info.

Return JSON with `action`, `confirmation_required`, and `result`.
"""

Examples:

1) status
- Input: {"action":"status"}
- Output: {"action":"status","result":{"exists":true,"status":"RUNNING","run_id":"20260813...","heartbeat_at":"...","loop_no":123}}

2) stop (requires confirmation)
- Assistant should prompt: "This will request the Supervisor to stop. Proceed?"
- If user confirms, call tool: {"action":"stop","confirm":true}
- Tool result: {"action":"stop","result":{"ok":true,"message":"Stop requested"}}

Use this tool only for supervisor control and status inspection.