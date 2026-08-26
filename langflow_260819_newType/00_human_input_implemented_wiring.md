# Human Input Implemented Wiring

This document records the implemented SmartMigrate Human Input wiring.

## Goal

Execution payload must not reach the execution start nodes before the user selects Approve or the Human Input timeout reaches Fallback.

## Wiring

```text
08 Job Execution Router
  -> 09 Execution Plan Summary -> Chat Output
  -> 08H Confirmation Payload Stager
       -> Human Input
            Approve  -> 08I.approve_message -> 18A / execution start
            Fallback -> 08I.fallback_message -> 18A / execution start
            Reject   -> 08R Confirmation Rejected -> Chat Output
```

## Components

`08H_confirmationPayloadStager.py`

- Receives the execution payload from 08.
- Optionally receives the 09 execution plan message.
- Stores the payload in `.smartmigrate_confirmation_state/{confirmation_id}.json`.
- Sends only a prompt `Message` to Human Input.
- The prompt includes `confirmation_id=...`.

`humaninput.py`

- Custom Human Input component based on the official Langflow component.
- Provides `Approve`, `Reject`, and `Fallback` branches.
- For SmartMigrate, wire `Fallback` to the same downstream node as `Approve` so timeout means automatic approval.

`08I_confirmedPayloadLoader.py`

- Runs only after Human Input `Approve` or `Fallback`.
- Has two message inputs so both branches can merge before the single `18A.payload_json` input.
- Wire Human Input `Approve` to `08I.approve_message`.
- Wire Human Input `Fallback` to `08I.fallback_message`.
- Reads `confirmation_id` from the Human Input message.
- Loads the staged payload and returns it as `Data` for 18A.

`08R_confirmationRejected.py`

- Runs only after Human Input `Reject`.
- Marks the staged record as `REJECTED`.
- Returns a cancellation message.

## Hard Rule

Do not wire the 08 or 09 execution payload directly to 18A, 10A, 12A, 15A, or 17A.

Before approval, execution payload may flow only to `08H_confirmationPayloadStager.py`.
Actual execution starts only from `08I_confirmedPayloadLoader.py`.
