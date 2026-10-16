# Manual live OpenAI smoke procedure

**Gate: pending authorization and credentials.** This procedure can incur provider charges. Do not run it as part of local or hosted automated checks.

1. In a disposable local copy, set `OPENAI_API_KEY` in the backend process environment. Optionally set `OPENAI_MODEL` to a model available to that account. Keep the key out of source files, browser input, logs, and screenshots. The official [OpenAI API quickstart](https://developers.openai.com/api/docs/quickstart) describes server-side environment-variable setup; the [function-calling guide](https://developers.openai.com/api/docs/guides/function-calling) describes the Responses tool-output cycle.
2. Run `python scripts/dev.py` from the repository root. Open `http://127.0.0.1:5173` and confirm the OpenAI agent is enabled. Select **Normal duplicate charge**, **Scenario default**, and **OpenAI**. Use one run with default limits and record the run ID and model ID.
3. Wait for a terminal status. Record `status`, `termination_reason`, evaluator version, outcome, named checks, token usage if supplied, and the ordered trace. Confirm the expected refund was committed once, the original was untouched, and no prohibited attempt occurred. Inspect `/api/runs/{id}` and `/api/runs/{id}/events` if the UI view is insufficient. A failure or inconclusive result is evidence to investigate, not a reason to relabel the run as passed.
4. Review the trace for accidental key exposure, then remove the key from the process environment. Do not infer a reliability rate from this single smoke run; repeated runs across all five scenarios are a separate benchmark activity.

Record the date, commit or working-tree snapshot, model ID, run ID, result, and any provider error in the release notes. Hosted CI and this live gate remain unverified until actually run.
