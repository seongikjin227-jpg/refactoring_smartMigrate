# Fail Analysis Tool

Tool name: fail_analysis_tool

Description:
Use this tool for failure-log lookups and aggregated failure analysis. When the user asks "why did this fail?", "show failure log for map_id X", or "analyze recent SQL conversion failures", the Chat Agent should select this tool.

System prompt (for LLM assistant selecting this tool):
"""
You are the Chat Agent and should choose `fail_analysis_tool` when the user requests failure investigation or aggregated failure analysis.

This tool supports the following actions:
- `query_failure_log`: Retrieve detailed failure logs for a specific `map_id` or `sql_id` (and optionally `space_nm`).
- `analyze_failures`: Produce an aggregated analysis of recent failures for a given agent (migration, sql_conversion, sql_tuning) including counts by fail-stage, top error messages, and suggested next checks.

Always return JSON with `action` and `result`.
"""

Action schemas and examples:

1) query_failure_log
- Input JSON: {"action":"query_failure_log","map_id":123}
- Or: {"action":"query_failure_log","sql_id":"SEL_001","space_nm":"userMapper"}
- Output example:
  {
    "action":"query_failure_log",
    "result":{
      "map_id":123,
      "job":{"fr_table":"A","to_table":"B","status":"FAIL"},
      "logs":[{"step":"MIGRATE","level":"ERROR","message":"ORA-XXXXX: ...","generated_sql_head":"INSERT ..."}, ...]
    }
  }

2) analyze_failures
- Input JSON: {"action":"analyze_failures","agent":"sql_conversion","limit":200}
- Output example:
  {
    "action":"analyze_failures",
    "result":{"total_fail":200,"by_stage":[{"stage":"BIND_SQL_GENERATION","count":45}],"top_messages":[{"message":"ORA-01008","count":32}],"hints":["Check bind parameter generation"]}
  }

Use this tool when the user explicitly requests failure investigation or aggregated failure statistics.