# Human Input Implemented Wiring

This document records the current SmartMigrate Human Input wiring.

## Goal

Execution payload must not reach `18A`, `10A`, `12A`, `15A`, or `17A` before the user selects `Approve` or the Human Input timeout reaches `Fallback`.

## Current Wiring

```text
08 Job Execution Router
  -> 08H Confirmation Prompt Builder
       -> 20 Human Input.prompt_message

08 Job Execution Router
  -> 20 Human Input.execution_data

20 Human Input
  Approve  -> 18A.execution_data / execution start
  Fallback -> 18A.execution_data / execution start
  Reject   -> 08R Confirmation Rejected -> Chat Output
```

## Components

`08H_confirmationPayloadStager.py`

- Receives the execution payload only to build a readable Korean plan message.
- Outputs a visible `Message` for Human Input.
- Does not embed base64, HTML comments, JSON markers, files, or state.
- The Human Input screen should end with: `진행 여부를 선택해주세요.`

`20_humanInput.py`

- Custom Human Input based on the official Langflow Human Input source shape.
- Uses `ActionPickerInput`, `BoolInput`, and `DurationInput`.
- Has one visible `Input` field backed by `prompt_message`; wire `08H.message` to it.
- Pauses the graph by calling `graph.request_pause(...)`.
- While waiting for a decision, stops all branch outputs so no empty Data can flow to Reject or execution nodes.
- Stops every non-selected output branch after resume.
- Carries `execution_data` as a `Data` input and emits it only through the selected branch.
- `Fallback` is timeout-based automatic approval. Default timeout is 30 seconds.

`11_finalDashboard.py`

- Receives loop Done only as a completion signal.
- Uses its own DB input fields for dashboard queries.
- Does not require `db_config` from loop Done payloads.

`08R_confirmationRejected.py`

- Receives the `Reject` branch `Data`.
- Returns only a cancellation message.
- Does not start any execution node.

## Hard Rule

Do not wire the `08` execution payload directly to execution start nodes.

The only allowed execution path is:

```text
08 Data -> 20 Human Input.execution_data
20 Approve/Fallback -> execution start
```

This keeps the payload invisible to the user while still preventing execution before approval.
