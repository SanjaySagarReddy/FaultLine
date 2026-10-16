# Faultline

Faultline is a local agent reliability workbench. It tests whether a tool-using agent leaves a simulated payment environment in the right state after faults and untrusted customer notes. A final message is shown, but the score comes from persisted business state and trace events.

## Run locally

Requirements: Python 3.13+, Node.js 22+, npm. From the repository root:

```sh
python scripts/dev.py
```

The command creates `.venv`, installs backend and frontend dependencies, applies the database migration, and starts the API and web UI. Open <http://127.0.0.1:5173>. Stop with Ctrl+C. The SQLite database is `backend/faultline.db` by default.

Choose Scenario default, No injected fault, or Custom single fault in the workbench. The API also accepts a validated fault_plan list on POST /api/runs; omitting it keeps the scenario default and an empty list disables faults. Any explicit plan remains in run history but does not enter the canonical five-scenario score, even if it equals the default. The selected-configuration header shows “Not in canonical suite” for an override; canonical cohort history remains visible below.

The deterministic baseline works without credentials. To enable the live LLM agent, set `OPENAI_API_KEY` on the backend process and optionally `OPENAI_MODEL` (default `gpt-5.6-terra`). The key never appears in API responses or traces. A live run uses the OpenAI Responses API and may incur provider charges. Use the web agent selector for a manual smoke run; CI uses a mocked provider response.

## Verify

```sh
cd backend
../.venv/Scripts/python.exe -m pytest -q
cd ../frontend
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

On macOS/Linux, use `../.venv/bin/python` instead. The browser test starts both servers automatically. CI runs backend tests, frontend tests/build, and the browser test.

## What is included

- Five versioned duplicate-payment scenarios: normal, transient failure, committed refund with lost response, malformed payment observation, and prompt injection in a customer note.
- Run-scoped SQLite fixtures and business tools, approved proposals, idempotent refunds, and a single queued-run worker.
- A deterministic baseline and a configurable OpenAI Responses adapter.
- State-based checks, safety and recovery checks, ordered traces, per-configuration suite scores across the current canonical scenario set, and comparisons that require equal benchmark conditions.
- A FastAPI API and React workbench.

## Release evidence

- [Task register](docs/task-register.md): T01–T13 status and code or test evidence.
- [Architecture](docs/architecture.md) and [decision log](docs/decisions.md): contracts, scoring choices, and limits.
- [Demo script](docs/demo-script.md): deterministic normal, response-loss, unsafe-attempt, and comparison runs.
- [Manual live OpenAI smoke procedure](docs/live-smoke.md): an authorization-dependent gate that is not part of automated tests.
- [Free deployment guide](docs/deployment.md): Render Blueprint, temporary history, and public-demo checks.
