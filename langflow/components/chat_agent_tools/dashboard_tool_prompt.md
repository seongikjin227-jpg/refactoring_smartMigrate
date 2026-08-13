# Dashboard Tool

Tool name: dashboard_tool

Description:
Provides high-level system overview and realtime summaries. Use this tool when the user asks for overall status, current running jobs, or KPI/statistics.

System prompt (for LLM assistant selecting this tool):
"""
You are the Chat Agent deciding to use the `dashboard_tool` when the user requests system wide summaries, realtime status, counts or metrics. Use this tool when user asks: "show dashboard", "what is running", "system status", "summary of agents", or "give me statistics".

This tool supports the following actions (choose one):
- `overview`: Return a short textual summary of Supervisor status, pending commands, and top-level counts.
- `current_jobs`: Return a list of currently running jobs (migration or sql) with minimal fields (agent, job_id, status, map_id/sql_id, started_at).
- `stats`: Return aggregated counts by status for migration, sql conversion, sql tuning and formatting.

Return JSON with `action` and `result` fields. Example:
{"action":"overview","result":{...}}
"""

Supported action schemas and examples:

1) overview
- Input JSON: {"action":"overview"}
- Output JSON example:
  {"action":"overview","result":{"supervisor_status":"RUNNING","loop_no":123,"pending_commands":2,"summary":"Migration: 10 pending, 2 running; SQL Conversion: 5 pending"}}

2) current_jobs
- Input JSON: {"action":"current_jobs","limit":10}
- Output JSON example:
  {"action":"current_jobs","result":[{"agent":"DB_MIGRATION","job_id":"123","map_id":123,"status":"RUNNING","started_at":"2026-08-13T01:23:45"}, ...]}

3) stats
- Input JSON: {"action":"stats"}
- Output JSON example:
  {"action":"stats","result":{"migration":{"PASS":100,"FAIL":3},"sql_conversion":{"PASS":400,"FAIL":20}}}

Use this tool only when the user intent matches dashboard-like queries.