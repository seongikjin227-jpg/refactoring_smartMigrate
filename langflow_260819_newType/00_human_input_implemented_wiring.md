# Human Input Implemented Wiring

This document records the implemented SmartMigrate Human Input wiring.

## Goal

Execution payload must not reach the execution start nodes before the user selects Approve or the Human Input timeout reaches Fallback.

## Wiring

```text
08 Job Execution Router
  -> 08H Confirmation Payload Stager
       -> 08H.prompt -> Human Input
       -> 08H.execution_payload -> 08I.payload_json
       -> Human Input
            Approve  -> 08I.approve_message -> 18A / execution start
            Fallback -> 08I.fallback_message -> 18A / execution start
            Reject   -> 08R Confirmation Rejected -> Chat Output
```

## Components

`08H_confirmationPayloadStager.py`

- Receives the execution payload from 08.
- Builds the execution plan message internally from route/count/job payload fields.
- Sends a prompt `Message` to Human Input.
- Sends the same execution payload as `Data` to `08I.payload_json`.
- The prompt includes `confirmation_id=...`.
- Does not write files or external state.

`humaninput.py`

- Custom Human Input component based on the official Langflow component.
- Provides `Approve`, `Reject`, and `Fallback` branches.
- For SmartMigrate, wire `Fallback` to the same downstream node as `Approve` so timeout means automatic approval.

`08I_confirmedPayloadLoader.py`

- Runs only after Human Input `Approve` or `Fallback`.
- Has `payload_json`, `approve_message`, and `fallback_message` inputs so both branches can merge before the single `18A.payload_json` input.
- Wire `08H.execution_payload` to `08I.payload_json`.
- Wire Human Input `Approve` to `08I.approve_message`.
- Wire Human Input `Fallback` to `08I.fallback_message`.
- Reads `confirmation_id` from the Human Input message or payload.
- Returns the direct `payload_json` as `Data` for 18A only after Approve/Fallback.
- Does not read or write files.

`08R_confirmationRejected.py`

- Runs only after Human Input `Reject`.
- Returns a cancellation message.
- Does not read or write files.

## Hard Rule

Do not wire the 08 execution payload directly to 18A, 10A, 12A, 15A, or 17A.

Before approval, execution payload may flow only to `08H_confirmationPayloadStager.py` and `08I_confirmedPayloadLoader.py`.
`08I` is the approval gate; it emits the actual execution payload only after `approve_message` or `fallback_message` arrives.
