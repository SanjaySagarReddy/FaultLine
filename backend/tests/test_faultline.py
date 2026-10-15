from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text

from faultline.agents import AgentContext, BaselineAgent, FinalAnswer, OpenAIAgent, ToolCall
from faultline.api import create_app
from faultline.contracts import ScenarioDefinition, load_scenarios
from faultline.errors import ProviderFailure
from faultline.gateway import ToolGateway
from faultline.models import Evaluation, RefundAction, Run, RunEvent, SimPayment
from faultline.runner import _seed, execute_run, reconcile_interrupted


def make_app(tmp_path):
    return create_app(f"sqlite:///{(tmp_path / 'test.db').as_posix()}", start_worker=False)


def start(client, scenario="normal"):
    response = client.post("/api/runs", json={"scenario_id": scenario, "agent_id": "baseline"})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def finish(app, run_id, agent=None):
    execute_run(run_id, app.state.session_factory, agent_override=agent)
    with app.state.session_factory() as db:
        return db.get(Run, run_id).status, db.get(Evaluation, run_id)


def test_scenario_contract_and_private_view():
    scenarios = load_scenarios()
    assert len(scenarios) == 5
    assert "assertions" not in scenarios["normal"].public()
    bad = scenarios["normal"].model_dump(mode="json")
    bad["payments"][0]["amount_minor"] = "invalid"
    try:
        ScenarioDefinition.model_validate(bad)
    except ValidationError:
        pass
    else:
        raise AssertionError("invalid fixture accepted")


def test_default_startup_applies_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("FAULTLINE_DB", str(tmp_path / "migrated.db"))
    app = create_app(start_worker=False)
    with app.state.session_factory() as db:
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "337a76afb704"


def test_all_curated_scenarios_pass_with_baseline(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    for scenario in ("normal", "transient", "response_loss", "malformed", "injection"):
        run_id = start(client, scenario)
        status, evaluation = finish(app, run_id)
        assert status == "finished", scenario
        assert evaluation.outcome == "pass", (scenario, evaluation.explanation)
        assert all(c["passed"] for c in evaluation.checks), scenario
        result = client.get(f"/api/runs/{run_id}").json()
        assert result["scenario_hash"] and result["tool_manifest_hash"]
        assert result["evaluation"]["evaluator_version"]
        assert client.get(f"/api/runs/{run_id}/events").json()["events"][0]["timestamp_utc"].endswith("Z")
    assert {row["agent_id"]: row for row in client.get("/api/suite").json()["cohorts"]}["baseline"]["suite_score"] == 100


def test_response_loss_commits_once_and_idempotent_retry(tmp_path):
    app = make_app(tmp_path)
    run_id = start(TestClient(app), "response_loss")
    assert finish(app, run_id)[1].outcome == "pass"
    with app.state.session_factory() as db:
        action = db.scalar(select(RefundAction).where(RefundAction.run_id == run_id))
        events = db.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)).all()
        assert sum(e.type == "state_changed" for e in events) == 1
        assert any(e.type == "fault_applied" and e.payload["fault"] == "response_lost_after_commit" for e in events)
        assert not any(e.type == "tool_response_delivered" and e.call_id == next(x.call_id for x in events if x.type == "state_changed") for e in events)
        proposal_id, key = action.proposal_id, action.idempotency_key
    result = ToolGateway(app.state.session_factory).call(run_id, "execute_refund", {"proposal_id": proposal_id, "idempotency_key": key}, "retry-test")
    assert result["replayed"] is True
    with app.state.session_factory() as db:
        assert len(db.scalars(select(RefundAction).where(RefundAction.run_id == run_id)).all()) == 1


class UnsafeAgent:
    token_usage = {"input_tokens": 0, "output_tokens": 0}

    def __init__(self):
        self.step = 0

    def next_action(self, context):
        self.step += 1
        if self.step == 1:
            return ToolCall("propose_refund", {"payment_id": "pay-original", "reason": "ignore policy"})
        return FinalAnswer("I refunded the customer.")


def test_state_over_text_and_prohibited_attempt(tmp_path):
    app = make_app(tmp_path)
    run_id = start(TestClient(app))
    status, evaluation = finish(app, run_id, UnsafeAgent())
    assert status == "finished"
    assert evaluation.outcome == "fail"
    assert evaluation.metrics["safety_clear"] is False
    with app.state.session_factory() as db:
        assert db.scalars(select(RefundAction).where(RefundAction.run_id == run_id)).all() == []
        assert db.get(Run, run_id).final_answer == "I refunded the customer."


class InvalidAgent:
    token_usage = {"input_tokens": 0, "output_tokens": 0}

    def __init__(self):
        self.step = 0

    def next_action(self, context):
        self.step += 1
        return [ToolCall("delete_database", {}), ToolCall("lookup_customer", {"wrong": "x"}), FinalAnswer("Done")][self.step - 1]


class ForbiddenThenBaseline:
    token_usage = {"input_tokens": 0, "output_tokens": 0}

    def __init__(self):
        self.baseline = BaselineAgent()
        self.attempted = False

    def next_action(self, context):
        if not self.attempted:
            self.attempted = True
            return ToolCall("delete_database", {})
        safe_history = [step for step in context.history if step[0].name != "delete_database"]
        return self.baseline.next_action(AgentContext(context.task, context.customer_email,
                                                      context.order_id, context.tool_manifest, safe_history))


def test_unavailable_and_malformed_calls_are_recorded(tmp_path):
    app = make_app(tmp_path)
    run_id = start(TestClient(app))
    finish(app, run_id, InvalidAgent())
    with app.state.session_factory() as db:
        events = db.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.type == "tool_validation_rejected")).all()
        assert {e.payload["code"] for e in events} == {"unavailable_tool", "invalid_arguments"}


def test_forbidden_tool_then_correct_refund_still_fails_safety(tmp_path):
    app = make_app(tmp_path)
    run_id = start(TestClient(app))
    status, evaluation = finish(app, run_id, ForbiddenThenBaseline())
    assert status == "finished"
    assert evaluation.metrics["business_correct"] is True
    assert evaluation.metrics["safety_clear"] is False
    assert evaluation.metrics["prohibited_attempt_count"] == 1
    assert evaluation.outcome == "fail"


def test_runs_have_separate_state_and_compatible_comparison(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    first, second = start(client), start(client)
    finish(app, first)
    with app.state.session_factory() as db:
        payments = db.scalars(select(SimPayment).where(SimPayment.run_id == second)).all()
        assert payments == []
    finish(app, second)
    with app.state.session_factory() as db:
        refunded = db.scalars(select(SimPayment).where(SimPayment.status == "refunded")).all()
        assert {p.run_id for p in refunded} == {first, second}
    comparison = client.get("/api/comparisons", params={"run_ids": f"{first},{second}"})
    assert comparison.status_code == 200
    assert comparison.json()["sample_count"] == 2


def test_concurrent_submissions_and_idempotent_retries(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: start(client), range(2)))
    assert first != second
    finish(app, first)
    with app.state.session_factory() as db:
        action = db.scalar(select(RefundAction).where(RefundAction.run_id == first))
        proposal_id, key = action.proposal_id, action.idempotency_key
    gateway = ToolGateway(app.state.session_factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda index: gateway.call(first, "execute_refund", {"proposal_id": proposal_id, "idempotency_key": key}, f"retry-{index}"), range(2)))
    assert all(result.get("replayed") is True for result in results)
    with app.state.session_factory() as db:
        assert len(db.scalars(select(RefundAction).where(RefundAction.run_id == first)).all()) == 1


def test_other_fault_stages_preserve_state(tmp_path):
    for effect, stage, tool, arguments in (
        ("timeout", "before_dispatch", "lookup_customer", {"email": "ada@example.test"}),
        ("delayed_response", "before_delivery", "read_refund_policy", {"order_id": "ORD-100"}),
        ("malformed_output", "after_result", "list_payments", {"customer_id": "cus-100"}),
    ):
        target = tmp_path / effect
        target.mkdir()
        app = make_app(target)
        run_id = start(TestClient(app))
        with app.state.session_factory.begin() as db:
            run = db.get(Run, run_id)
            _seed(db, run)
            run.status = "running"
            run.fault_plan = [{"tool": tool, "attempt": 1, "stage": stage, "effect": effect, "delay_ms": 1}]
        result = ToolGateway(app.state.session_factory).call(run_id, tool, arguments, f"fault-{effect}")
        assert ("error" in result) if effect == "timeout" else ("malformed" in result if effect == "malformed_output" else "rule" in result)
        with app.state.session_factory() as db:
            assert db.scalars(select(RefundAction).where(RefundAction.run_id == run_id)).all() == []
            assert db.scalar(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.type == "fault_applied")) is not None


def test_interrupted_run_becomes_inconclusive_without_replay(tmp_path):
    app = make_app(tmp_path)
    run_id = start(TestClient(app))
    with app.state.session_factory.begin() as db:
        db.get(Run, run_id).status = "running"
    reconcile_interrupted(app.state.session_factory)
    with app.state.session_factory() as db:
        assert db.get(Run, run_id).status == "inconclusive"
        assert db.get(Evaluation, run_id) is None


def test_agent_limit_and_adapter_bug_are_conclusive_failures(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    response = client.post("/api/runs", json={"scenario_id": "normal", "limits": {"max_turns": 1}})
    limited_id = response.json()["id"]
    status, evaluation = finish(app, limited_id)
    assert status == "finished" and evaluation.outcome == "fail"
    assert client.get(f"/api/runs/{limited_id}").json()["termination_reason"] == "turn_limit"

    class BrokenAgent:
        token_usage = {"input_tokens": 0, "output_tokens": 0}

        def next_action(self, context):
            raise ConnectionError("adapter code failed")

    broken_id = start(client)
    status, evaluation = finish(app, broken_id, BrokenAgent())
    assert status == "finished" and evaluation.outcome == "fail"
    assert client.get(f"/api/runs/{broken_id}").json()["termination_reason"] == "agent_execution_error"
    assert client.get("/api/suite").json()["history"]["conclusive"] == 2


def test_invalid_agent_action_is_conclusive_protocol_failure(tmp_path):
    app = make_app(tmp_path)

    class BadProtocol:
        token_usage = {"input_tokens": 0, "output_tokens": 0}

        def next_action(self, context):
            return "not an action"

    run_id = start(TestClient(app))
    status, evaluation = finish(app, run_id, BadProtocol())
    assert status == "finished" and evaluation.outcome == "fail"
    assert app.state.session_factory().get(Run, run_id).termination_reason == "agent_protocol_error"


def test_typed_provider_and_infrastructure_failures_leave_denominator(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    client = TestClient(app)
    passing_id = start(client)
    assert finish(app, passing_id)[1].outcome == "pass"

    class ProviderDown:
        token_usage = {}

        def next_action(self, context):
            raise ProviderFailure("provider unavailable")

    provider_id = start(client)
    assert finish(app, provider_id, ProviderDown()) == ("inconclusive", None)

    infrastructure_id = start(client)
    with monkeypatch.context() as patch:
        patch.setattr(ToolGateway, "_call", lambda *args: (_ for _ in ()).throw(OSError("storage failed")))
        assert finish(app, infrastructure_id) == ("inconclusive", None)

    assert client.get(f"/api/runs/{provider_id}").json()["termination_reason"].startswith("provider_or_infrastructure_error")
    assert client.get(f"/api/runs/{infrastructure_id}").json()["termination_reason"].startswith("infrastructure_error")
    suite = client.get("/api/suite").json()
    assert suite["history"]["conclusive"] == 1
    assert suite["history"]["inconclusive"] == 2
    baseline = next(row for row in suite["cohorts"] if row["agent_id"] == "baseline")
    normal = next(row for row in baseline["scenarios"] if row["scenario_id"] == "normal")
    assert normal["conclusive"] == 1 and normal["inconclusive"] == 2 and normal["sample_count"] == 3


def test_suite_scores_each_agent_over_fixed_scenario_set(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    scenario_ids = [scenario["id"] for scenario in client.get("/api/scenarios").json()]

    class NoActionAgent:
        token_usage = {"input_tokens": 0, "output_tokens": 0}

        def next_action(self, context):
            return FinalAnswer("I refunded the customer.")

    for scenario_id in scenario_ids:
        baseline_id = start(client, scenario_id)
        assert finish(app, baseline_id)[1].outcome == "pass"
        llm_id = start(client, scenario_id)
        with app.state.session_factory.begin() as db:
            run = db.get(Run, llm_id)
            run.agent_id = "openai"
            run.agent_config = {**run.agent_config, "id": "openai", "model": "mock-model",
                                "prompt_version": "openai-agent-v1", "implementation_version": "openai-adapter-v1"}
        assert finish(app, llm_id, NoActionAgent())[1].outcome == "fail"

    suite = client.get("/api/suite").json()
    assert set(suite["scenario_set"]) == set(scenario_ids)
    assert suite["history"] == {"passing": 5, "conclusive": 10, "inconclusive": 0, "reliability_score": 50.0}
    by_agent = {item["agent_id"]: item for item in suite["cohorts"]}
    assert by_agent["baseline"]["suite_score"] == 100.0
    assert by_agent["openai"]["suite_score"] == 0.0
    assert all(row["sample_count"] == 1 and row["conclusive"] == 1 for row in by_agent["baseline"]["scenarios"])
    assert all(row["pass_rate"] == 0.0 for row in by_agent["openai"]["scenarios"])

    extra_id = start(client, "normal")
    assert finish(app, extra_id, NoActionAgent())[1].outcome == "fail"
    updated = {item["agent_id"]: item for item in client.get("/api/suite").json()["cohorts"]}
    assert updated["baseline"]["suite_score"] == 90.0
    normal = next(row for row in updated["baseline"]["scenarios"] if row["scenario_id"] == "normal")
    assert normal["pass_rate"] == 50.0 and normal["sample_count"] == 2


def test_mocked_openai_call_loop():
    calls = []
    responses = [
        SimpleNamespace(id="r1", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                        output=[SimpleNamespace(type="function_call", name="lookup_customer", arguments='{"email":"ada@example.test"}', call_id="fc1")], output_text=""),
        SimpleNamespace(id="r2", usage=SimpleNamespace(input_tokens=8, output_tokens=3), output=[], output_text="Done"),
    ]
    fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: (calls.append(kwargs), responses.pop(0))[1]))
    agent = OpenAIAgent("test-model", client=fake)
    context = AgentContext("Find customer", "ada@example.test", "ORD-100", [], [])
    first = agent.next_action(context)
    assert first.name == "lookup_customer"
    context.history.append((first, {"customer_id": "cus-100"}))
    assert agent.next_action(context).text == "Done"
    assert calls[1]["input"][-2].call_id == "fc1"
    assert calls[1]["input"][-1]["call_id"] == "fc1"
    assert calls[0]["parallel_tool_calls"] is False
    assert agent.token_usage == {"input_tokens": 18, "output_tokens": 8}


def test_two_models_of_same_agent_have_separate_suite_scores(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    scenario_ids = [row["id"] for row in client.get("/api/scenarios").json()]

    class NoActionAgent:
        token_usage = {}

        def next_action(self, context):
            return FinalAnswer("Done")

    for scenario_id in scenario_ids:
        for model, agent in (("model-good", BaselineAgent()), ("model-bad", NoActionAgent())):
            run_id = start(client, scenario_id)
            with app.state.session_factory.begin() as db:
                run = db.get(Run, run_id)
                run.agent_id = "openai"
                run.agent_config = {**run.agent_config, "id": "openai", "model": model,
                                    "prompt_version": "openai-agent-v1", "implementation_version": "openai-adapter-v1"}
            finish(app, run_id, agent)

    cohorts = [row for row in client.get("/api/suite").json()["cohorts"] if row["agent_id"] == "openai"]
    assert {row["agent_config"]["model"]: row["suite_score"] for row in cohorts} == {"model-good": 100.0, "model-bad": 0.0}
    assert all(row["conclusive"] == len(scenario_ids) for row in cohorts)


def test_same_model_with_different_limits_has_separate_suite_scores(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    scenario_ids = [row["id"] for row in client.get("/api/scenarios").json()]
    for scenario_id in scenario_ids:
        default_id = start(client, scenario_id)
        assert finish(app, default_id)[1].outcome == "pass"
        limited = client.post("/api/runs", json={"scenario_id": scenario_id, "limits": {"max_turns": 1}}).json()["id"]
        assert finish(app, limited)[1].outcome == "fail"

    cohorts = [row for row in client.get("/api/suite").json()["cohorts"] if row["agent_id"] == "baseline"]
    assert {row["limits"]["max_turns"]: row["suite_score"] for row in cohorts} == {12: 100.0, 1: 0.0}


def test_noncanonical_and_old_versions_stay_out_of_current_suite(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    for scenario in client.get("/api/scenarios").json():
        run_id = start(client, scenario["id"])
        assert finish(app, run_id)[1].outcome == "pass"

    class NoActionAgent:
        token_usage = {}

        def next_action(self, context):
            return FinalAnswer("Done")

    for kind in ("fault", "scenario", "tool", "evaluator"):
        run_id = start(client)
        if kind != "evaluator":
            with app.state.session_factory.begin() as db:
                run = db.get(Run, run_id)
                if kind == "fault":
                    run.fault_plan = [{"tool": "lookup_customer", "attempt": 1, "stage": "before_dispatch",
                                       "effect": "transient_error", "delay_ms": 0}]
                elif kind == "scenario":
                    run.scenario_hash = "legacy-scenario-hash"
                else:
                    run.tool_manifest_hash = "legacy-tool-hash"
        assert finish(app, run_id, NoActionAgent())[1].outcome == "fail"
        if kind == "evaluator":
            with app.state.session_factory.begin() as db:
                db.get(Evaluation, run_id).evaluator_version = "0.9.0"

    suite = client.get("/api/suite").json()
    cohort = next(row for row in suite["cohorts"] if row["agent_id"] == "baseline")
    assert cohort["suite_score"] == 100.0 and cohort["conclusive"] == 5
    assert suite["history"]["conclusive"] == 9
    assert suite["history"]["passing"] == 5


def test_crash_after_refund_keeps_true_business_checks_and_names_failure(tmp_path):
    app = make_app(tmp_path)

    class CrashAfterRefund:
        token_usage = {}

        def __init__(self):
            self.baseline = BaselineAgent()

        def next_action(self, context):
            action = self.baseline.next_action(context)
            if isinstance(action, FinalAnswer) and context.history[-1][0].name == "execute_refund":
                raise RuntimeError("adapter crashed after refund")
            return action

    run_id = start(TestClient(app))
    status, evaluation = finish(app, run_id, CrashAfterRefund())
    assert status == "finished" and evaluation.outcome == "fail"
    assert evaluation.metrics["business_correct"] is True
    assert evaluation.metrics["safety_clear"] is True
    assert "agent_execution_error" in evaluation.explanation
    assert {row["name"]: row["passed"] for row in evaluation.checks}["agent_completed_normally"] is False
