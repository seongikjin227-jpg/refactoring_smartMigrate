# Loop Output Test Plan - 260831

## 0. Current Test Status And Conclusion

### Current Status

| Item | Component/File | Status | Result |
|---|---|---|---|
| A | `04A_sleep_interval_output_probe.py` | Failed | `sleep()` interval did not make Chat Output appear step-by-step. Output was shown once after the component returned. |
| D probe | `04D_in_flow_stream_probe.py` | Failed/Negative | `stream-probe step 1/5` through `step 5/5` appeared all at once. Intermediate `self.log()` and `self.status` updates were not rendered as Chat Output messages. |
| D control | `04D_hard_sleep_output_probe.py` | Ready | Hard-coded 5-second sleep probe. Use only to confirm elapsed time and node refresh behavior. |
| C | `04C_progress_side_channel_sink.py` | Ready/Not tested | Stores progress outside Chat Output. This is the most realistic fallback if API streaming cannot be used. |
| D API | `04D_v2_workflow_background_tester.py` | Ready/Not tested | Requires checking whether the Langflow server/platform can use v2 workflow stream/background APIs. |

### Current Conclusion

`sleep()` is not a streaming implementation. It only delays execution.

The current test result shows this behavior:

```text
Custom component starts
custom component sleeps between internal steps
custom component returns one final Message
Chat Output displays that final Message once
```

So `sleep()` can only test whether an existing flush/event path is already available. It cannot create partial streaming by itself.

Current conclusion:

```text
The current Langflow Playground/Chat Output path does not flush custom component intermediate progress as chat messages.
Using only sleep/interval inside the loop is not enough for real-time progress during a 4-5 hour workflow.
```

Practical remaining options:

1. Check whether the platform can call Langflow v2 Workflow API in `stream` or `background` mode.
2. If the platform caller cannot change, write progress events to a side-channel such as DB/API and let the platform poll them.
3. Investigate whether Langflow exposes an internal event/message API that can explicitly emit intermediate Playground messages. No confirmed supported pattern has been found yet.

### Answer Draft For Senior Engineer

```text
We tested the sleep/interval approach inside the loop/component.
The sleep delayed execution, but Chat Output did not flush intermediate steps.
In Langflow Playground, the steps were displayed all at once after the final return.

Therefore, sleep/interval alone cannot provide real-time progress output in the current Chat Output path.

Next checks:
1. Confirm whether the platform caller can use Langflow v2 Workflow API stream/background events.
2. If caller changes are difficult, store progress per loop iteration in a DB/API side-channel and let the platform poll it.
```

## 0.1 Questions For Platform/Langflow Owner

These questions should be asked to the platform/Langflow owner because the caller and platform UI behavior are outside this component code.

1. Which endpoint does the internal platform use to call Langflow?
   - `/api/v1/run/{flow_id}` final-response mode?
   - `/api/v1/run/{flow_id}?stream=true` streaming mode?
   - `/api/v2/workflows` workflow API?

2. Does the platform HTTP client actually read streaming responses?
   - Does it use SSE/EventSource?
   - Does it use `requests.post(..., stream=True)` or an equivalent streaming reader?
   - Is reverse proxy / nginx / API gateway buffering enabled?

3. What real-time update pattern does the platform chat UI support?
   - Partial update of the same chat bubble?
   - Appending new assistant messages?
   - Only one final assistant message?

4. Is Langflow v2 Workflow API available on the server?
   - What is the Langflow server version?
   - Is `LANGFLOW_DEVELOPER_API_ENABLED=true` enabled?
   - Is `POST /api/v2/workflows` allowed by API key and network policy?

5. Can long-running workflows use background job/status polling?
   - Can caller receive `job_id` from `POST /api/v2/workflows`?
   - Can caller poll `GET /api/v2/workflows?job_id=...`?
   - Can caller attach to `GET /api/v2/workflows/{job_id}/events`?

6. If changing the platform Langflow caller is difficult, can the platform add a separate progress read path?
   - DB table polling?
   - Internal API polling?
   - Redis/message queue progress store?

7. Can the platform and Langflow share a progress correlation key?
   - Candidate keys: `run_key`, `session_id`, `job_id`
   - A common key is required to match one user workflow run to progress rows/events.

## 1. Goal

Full Workflow loop is currently connected like this:

```text
18D -> Chat Output
18D -> 18B loop feedback
```

Langflow Playground and the service appear to show only the final Chat Output message. The goal is to verify whether there is any way to show per-job progress in real time during a 4-5 hour loop.

## 2. What sleep() Actually Tests

`sleep()` does not create streaming.

It only pauses the current Python function before it returns. It is useful only as a probe:

```text
If the platform/Playground already flushes intermediate component events,
then a delayed component may make those events visibly appear one by one.
```

But if Langflow waits for the component method to finish and only then sends the returned `Message` to Chat Output, then `sleep()` only delays the final single response.

## 3. A Test Result - Sleep Interval

Tested components:

```text
04A_sleep_interval_output_probe.py
04D_in_flow_stream_probe.py
```

Observed result:

```text
stream-probe step 1/5 ... step 5/5 appeared all at once.
04A sleep interval output probe also failed to produce partial Chat Output.
```

Interpretation:

1. The visible Chat Output was emitted once at the end, not one message per step.
2. `self.log()` and `self.status` updates did not become Chat Output messages.
3. `sleep()` is not enough to create streaming.

Follow-up control test:

```text
04D_hard_sleep_output_probe.py
```

This hard-codes 5-second sleeps. With `probe_steps=5`, total runtime should be about 20 seconds. If it still outputs only once at the end, the conclusion is confirmed:

```text
Custom component Message output is delivered to Chat Output only after the output method returns.
```

## 4. Current Hypothesis

Chat Output is not a partial-streaming component for arbitrary custom component messages.

The official Langflow v1 run API supports `?stream=true`, but the documentation describes this as LLM token response streaming. It does not show a supported pattern where a custom Chat Output component can yield multiple partial chat messages during one output method.

The Langflow v2 Workflow API supports `sync`, `stream`, and `background` modes. In v2 background mode, a caller can get a `job_id`, poll `GET /api/v2/workflows?job_id=...`, or re-attach to `GET /api/v2/workflows/{job_id}/events`.

That is promising, but it still requires the caller to use the v2 API or a separate tester to call it.

## 5. Test Matrix

| Test | Method | Status | Conclusion |
|---|---|---|---|
| A | `sleep()` inside custom component | Failed | Did not produce partial Chat Output in Playground |
| A-control | hard-coded 5 sec sleep | Pending | Confirms whether UI parameter was ignored |
| C | side-channel progress sink | Ready | Most reliable without relying on Chat Output streaming |
| D-v1 | `/api/v1/run/{flow_id}?stream=true` | Ready | Mainly LLM token streaming, may not expose job progress |
| D-v2-stream | `POST /api/v2/workflows` with `mode=stream` | Ready | Needs developer API enabled |
| D-v2-background | `POST /api/v2/workflows` with `mode=background`, then poll/events | Ready | Best API-level test for long-running jobs |

## 6. Replacement Options In The Flow

### A - Sleep Probe

```text
18D.Message -> 04A Sleep Interval Output Probe.message_payload
18D.Loop Result -> 04A Sleep Interval Output Probe.loop_result_input
04A.Message -> Chat Output
04A.Loop Result -> 18B
```

Expected answer:

```text
This only tests whether Chat Output or Playground flushes intermediate execution state.
It does not implement streaming by itself.
```

### C - Side-Channel Progress

```text
18D.Message -> 04C Progress Side Channel Sink.message_payload
18D.Loop Result -> 04C Progress Side Channel Sink.loop_result_input
04C.Message -> Chat Output
04C.Loop Result -> 18B
```

Start with:

```text
sink_mode = FILE
output_file = progress_events.jsonl
```

If this works, use DB mode:

```text
sink_mode = ORACLE_DB
progress_table = NEXT_WORKFLOW_PROGRESS
```

This does not depend on Chat Output partial streaming. It writes progress per job somewhere else, and the platform can read that progress separately.

### D - API-Level Streaming/Background

This is not a Chat Output component change.

This tests whether Langflow's API can expose live events when the caller uses:

```text
POST /api/v2/workflows
mode=stream
```

or:

```text
POST /api/v2/workflows
mode=background
GET /api/v2/workflows?job_id=...
GET /api/v2/workflows/{job_id}/events
```

Important caution:

Do not call `/api/v2/workflows` for the same long workflow from inside the same v1 flow as the last component. That starts another workflow execution and can cause duplicate execution or recursion. The v2 call should be made by a test script or by the platform caller.
