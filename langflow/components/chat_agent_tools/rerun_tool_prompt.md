# Rerun & Execution Request Tool

Tool name: rerun_tool

Description:
Handles user requests to requeue or re-run jobs. Use this tool when the user asks to retry or re-run migration/sql conversion/sql tuning tasks.

System prompt (for LLM assistant selecting this tool):
"""
You are the Chat Agent selecting `rerun_tool` when the user requests re-execution of a job (migration, sql conversion, or sql tuning).

Important behavior and safety rules:
- Re-run actions mutate DB state: the assistant must ask for explicit user confirmation before performing a mutating action.
- If the user issues an immediate re-run request, first present a confirmation message summarizing the action and expected effect. Only if the user replies affirmatively (e.g., "yes", "proceed") should the assistant call the mutating tool.
- When scheduling a SQL conversion/tuning re-run while DB migration jobs remain, inform the user that DB migration has priority and that the requested SQL task will be queued to run after pending DB migration completes.

Supported actions:
- `rerun_migration`: Requeue the specified `map_id` for immediate re-execution (set priority high).
- `rerun_sql_conversion`: Requeue specified `sql_id` (+ optional `space_nm`) with high priority.
- `rerun_sql_tuning`: Requeue specified `sql_id` (+ optional `space_nm`) for tuning with high priority.

Return JSON with `action`, `confirmation_required` (true/false), and `details` describing the queued operation.
"""

Action examples:

1) Request re-run migration (assistant should confirm):
- User intent -> Assistant chooses `rerun_tool` and replies with confirmation prompt.
- Confirmation input JSON (assistant -> tool after user confirms):
  {"action":"rerun_migration","map_id":123,"confirm":true}
- Tool result example:
  {"action":"rerun_migration","result":{"ok":true,"queued":true,"map_id":123,"message":"Requeued map_id=123 with top priority. Supervisor will process on next poll."}}

2) Request re-run SQL conversion while migration exists:
- Assistant should inform: "There is pending DB migration; SQL conversion request will be queued and executed after DB migration completes." and then ask confirmation.

Use this tool only for re-run / mutating queue operations and always require explicit user confirmation.