# Human Input Implemented Wiring

This document records the active experimental wiring for per-job Full Workflow HITL.

## Goal

After every 18B loop item, show the human:

- current full-workflow progress
- current phase and route progress
- the exact job that is about to run
- Approve / Reject / Fallback choices

The original job item must continue to the executor only after `Approve` or timeout `Fallback`.

## Wiring

```text
18B Full Workflow Loop.item
  -> 20A Full Workflow HITL Prompt.payload_json

20A Full Workflow HITL Prompt.prompt_message
  -> 20B Full Workflow Human Input.prompt_message

20A Full Workflow HITL Prompt.job_item
  -> 20B Full Workflow Human Input.job_item

20B Full Workflow Human Input.branch_approve
  -> 10C MIG One Job POC Executor.job_item

20B Full Workflow Human Input.branch_fallback
  -> 10C MIG One Job POC Executor.job_item
```

Do not connect `20B.branch_reject` to 10C. If rejection needs a visible output, connect it to a separate manual-review/rejection message path only.

## Components

`20A_fullWorkflowHitlPrompt.py`

- Input: `payload_json`
- Output: `prompt_message` as `Message`
- Output: `job_item` as `Data`
- Uses `job_index`, `total_jobs`, `completed_before`, `phase_index`, `route_job_index`, and `workflow_plan_counts` when present.

`20B_fullWorkflowHumanInput.py`

- Input: `prompt_message`
- Input: `job_item`
- Outputs: `branch_approve`, `branch_reject`, `branch_fallback`
- Default `enable_fallback=True`
- Default `timeout={"value": 30, "unit": "Seconds"}`
- `Approve` returns original job item with `hitl_status=APPROVED`
- `Fallback` returns original job item with `hitl_status=APPROVED_BY_TIMEOUT`
- `Reject` returns a rejected payload and should not be wired to an executor

## Propagation Rule

While waiting for human input, 20B stops every branch output. After resume, 20B stops every non-selected branch. This avoids the earlier problem where unrelated chat outputs or execution paths received empty data after a paused run resumed.

## Hard Rule

Never wire:

```text
18B item -> 10C
```

Always wire:

```text
18B item -> 20A -> 20B Approve/Fallback -> 10C
```
