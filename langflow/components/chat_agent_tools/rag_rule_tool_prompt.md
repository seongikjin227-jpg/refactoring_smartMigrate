# RAG Rule Search Tool

Tool name: rag_rule_tool

Description:
Search and inspect RAG rules used for SQL conversion/tuning. Use this tool when the user asks to find rules, view guidance text, or see example source/target SQL.

System prompt (for LLM assistant selecting this tool):
"""
Select `rag_rule_tool` when the user asks about RAG rules, tuning rules, or wants guidance examples from registered rules.

Supported actions:
- `search_rules`: Search rules by keyword across RAG_ID, SOURCE_TABLES, GUIDANCE_TEXT, SOURCE_SQL, TARGET_SQL.
- `get_rule`: Retrieve full rule details for a given `rag_id`.
- `top_rules`: Return top N rules by hit count.

Return JSON with `action` and `result`.
"""

Action examples:

1) search_rules
- Input JSON: {"action":"search_rules","keyword":"sequence","category":"SQL_TUNING"}
- Output:
  {"action":"search_rules","result":[{"rag_id":101,"category":"SQL_TUNING","rule_type":"GENERAL","guidance_text":"Avoid full table scan ..."}, ...]}

2) get_rule
- Input JSON: {"action":"get_rule","rag_id":101}
- Output:
  {"action":"get_rule","result":{"rag_id":101,"category":"SQL_TUNING","source_sql":"SELECT ...","target_sql":"SELECT ...","guidance_text":"...","hit_cnt":42}}

3) top_rules
- Input JSON: {"action":"top_rules","limit":5}
- Output:
  {"action":"top_rules","result":[{"rag_id":12,"hit_cnt":201}, ...]}

Prefer `rag_rule_tool` responses to include clear pointers to `SOURCE_SQL` and `TARGET_SQL` when available.