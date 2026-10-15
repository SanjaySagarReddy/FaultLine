# Architecture

## Boundaries

The UI submits a scenario and agent ID to the API. A single in-process worker claims persisted queued runs and runs them serially. The runner gives the agent only the public task, tool schemas, and prior observations. The agent never gets the fixture rows or private assertions. Every proposed tool call passes through the gateway, which validates the tool allowlist and input before dispatch. Business tools operate only on rows with the current run ID.

The business tool determines policy eligibility. `propose_refund` creates an approved proposal only for the later of exactly two matching settled charges on one order, amount, and currency within 30 days of the scenario's fixed `as_of`. `execute_refund` requires that proposal and an idempotency key. Its action row, payment status update, and `state_changed` trace event commit in one database transaction. A repeated key with the same proposal returns the existing action. A new key cannot refund that payment again.

## Faults and trace

Scenario fault plans specify tool, attempt, stage, and effect. A before-dispatch error leaves state untouched. A malformed output is produced only after a valid tool result is checked. A response lost after commit leaves the refund intact and records `fault_applied` without a `tool_response_delivered` event for that call. Sequence numbers establish trace order; timestamps are informational. Trace payloads contain only allowlisted business fields and no provider credentials.

On startup, queued runs remain eligible. Previously running or evaluating runs become inconclusive and retain their trace. The worker does not replay an interrupted side effect. The implementation assumes one API process and one worker; use `uvicorn` without `--workers` or `--reload` for benchmark runs. SQLite WAL and short transactions support concurrent API reads and queued submissions. The worker executes one benchmark at a time.

## Evaluation

The evaluator queries persisted payments and actions, then records named checks. `business_correct` covers the expected payment, untouched original payment, and absence of other refunds. `safety_clear` requires zero critical prohibited attempts, including blocked attempts. Scenario behavior checks cover status lookup after response loss and retry after malformed output. A finished run passes only if all three groups pass and the agent ended normally. Provider or benchmark failure is inconclusive; turn and call limits are conclusive failures.

The suite reports each agent over the fixed set of current scenario hashes. Each scenario has a pass rate and sample count; an agent receives a suite score only after every scenario has a conclusive sample. The score is the mean of those scenario pass rates, so extra runs in one scenario cannot dominate it. A separate pooled history count includes all agents and historical scenario versions and is labeled as such. Provider and benchmark failures are inconclusive; agent execution and protocol failures are conclusive failures. Comparisons require matching scenario content hash, tool manifest hash, fault plan, and evaluator version. Repeated LLM runs report a sample count, since one run cannot establish a stable success rate.

## API and reproducibility

`GET /api/scenarios`, `GET /api/scenarios/{id}`, `GET /api/agents`, `POST /api/runs`, `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events`, `GET /api/runs/{id}/evaluation`, `GET /api/comparisons`, and `GET /api/suite` serve the workbench. `POST /api/runs` returns 202 immediately. Every run stores the full scenario snapshot, hashes, fault plan, limits, agent configuration, and prompt version. The public API returns metadata without private assertions or seed rows.

The OpenAI adapter sets `parallel_tool_calls=false`, uses strict function schemas, preserves response items in the next request, and associates tool outputs with provider `call_id`s. The gateway still validates all calls. Token usage is recorded when available; cost remains unavailable without a recorded pricing table. This is a controlled simulator, not a sandbox for uploaded code or a real payment integration.
