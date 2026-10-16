# Task register

This register uses the architecture handoff's T01–T13 objective sequence and links each task to the implementation now in the repository. “Locally verified” means the listed automated checks pass in this workspace; it does not mean hosted CI or a live provider run passed.

| ID | Scope | Status | Evidence |
| --- | --- | --- | --- |
| T01 | Scenario and tool contracts | Locally verified | [Contracts](../backend/faultline/contracts.py), [validation tests](../backend/tests/test_faultline.py) |
| T02 | Run-scoped payment state and migrations | Locally verified | [Models](../backend/faultline/models.py), [migrations](../backend/migrations/versions/337a76afb704_initial_schema.py), [isolation tests](../backend/tests/test_faultline.py) |
| T03 | Policy-compliant, idempotent refunds | Locally verified | [Business dispatch](../backend/faultline/gateway.py), [refund tests](../backend/tests/test_faultline.py) |
| T04 | Fault injector and tool gateway | Locally verified | [Gateway and fault lifecycle](../backend/faultline/gateway.py), [fault and safety tests](../backend/tests/test_faultline.py) |
| T05 | Deterministic baseline agent | Locally verified | [Baseline agent](../backend/faultline/agents.py), [five-scenario baseline test](../backend/tests/test_faultline.py) |
| T06 | Queued runner, deadlines, and recovery after interruption | Locally verified | [Runner](../backend/faultline/runner.py), [deadline tests](../backend/tests/test_faultline.py) |
| T07 | State, safety, and recovery evaluator | Locally verified | [Evaluator](../backend/faultline/evaluation.py), [adversarial tests](../backend/tests/test_faultline.py) |
| T08 | Five curated scenarios | Locally verified | [Scenario definitions](../backend/scenarios), [five-scenario baseline test](../backend/tests/test_faultline.py) |
| T09 | Live LLM adapter | Mock verified; live pending | [OpenAI adapter](../backend/faultline/agents.py), [mock-provider tests](../backend/tests/test_faultline.py), [live smoke procedure](live-smoke.md) |
| T10 | API | Locally verified | [API routes](../backend/faultline/api.py), [API tests](../backend/tests/test_faultline.py) |
| T11 | Workbench UI | Locally verified | [Workbench](../frontend/src/App.tsx), [UI tests](../frontend/src/App.test.tsx), [browser tests](../frontend/e2e/run.spec.ts) |
| T12 | Run comparison | Locally verified | [Comparison API](../backend/faultline/api.py), [canonical suite](../backend/faultline/suite.py), [compatibility tests](../backend/tests/test_faultline.py) |
| T13 | Release readiness | Local gates verified; hosted gates and deployment pending | [Setup command](../scripts/dev.py), [demo](demo-script.md), [decision log](decisions.md), [CI workflow](../.github/workflows/ci.yml), [deployment plan](deployment.md) |

Before release, verify the [remaining gates](live-smoke.md): hosted CI on the intended commit and an authorized live OpenAI smoke run. Before using the project as a public resume demo, complete the [deployment checks](deployment.md). Local checks and clean-install results should be recorded with the release candidate; they do not replace those external gates.

## Local verification on 2026-10-16

On Windows with Python 3.13.12, Node 22.13.1, and npm 10.9.2, backend pytest passed 49 tests, Vitest passed 5 tests, the frontend build passed, and Playwright passed 2 tests. The new backend checks cover same-origin static serving and baseline-only public demo enforcement. An isolated copy excluding `.venv`, `node_modules`, build output, and databases previously launched via `python scripts/dev.py`; the UI returned HTTP 200, API health returned `ok`, and a baseline normal run finished with evaluator 1.3.0 and outcome `pass`. The [deterministic demo runner](../scripts/demo_runs.py) previously completed its four runs and compatible comparison. The [CI workflow](../.github/workflows/ci.yml) contains the corresponding install, test, build, browser, and container-build commands, but hosted CI has not been run for this working tree. Docker Desktop's Linux engine was unavailable locally, so the container image remains unbuilt here.
