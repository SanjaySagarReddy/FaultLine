from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import re
import pytest
from types import SimpleNamespace
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text

from faultline.agents import AGENT_IMPLEMENTATION_VERSIONS, BASELINE_PROMPT_VERSION, AgentContext, BaselineAgent, FinalAnswer, OpenAIAgent, ToolCall
from faultline.api import LimitsInput, create_app
from faultline.contracts import ScenarioDefinition, canonical_hash, load_scenarios, tool_contract_hash
from faultline.errors import ProviderFailure
from faultline.evaluation import EVALUATOR_VERSION
import faultline.gateway as gateway_module
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
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "b8e9a2c4d6f0"


def test_pre_phase_b_run_migrates_as_legacy_and_stays_out_of_current_cohorts(tmp_path, monkeypatch):
    db_path = tmp_path / "pre_phase_b.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(config, "337a76afb704")

    scenario = load_scenarios()["normal"]
    snapshot = scenario.model_dump(mode="json")
    legacy_id = "pre-phase-b-run"
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO runs
            (id, scenario_id, scenario_version, scenario_hash, scenario_snapshot,
             tool_manifest_hash, agent_id, agent_config, fault_plan, limits, status,
             termination_reason, created_at, event_seq)
            VALUES (:id, :scenario_id, :scenario_version, :scenario_hash, :scenario_snapshot,
                    :tool_manifest_hash, :agent_id, :agent_config, :fault_plan, :limits, :status,
                    :termination_reason, :created_at, :event_seq)"""), {
            "id": legacy_id, "scenario_id": scenario.id, "scenario_version": scenario.version,
            "scenario_hash": canonical_hash(snapshot), "scenario_snapshot": json.dumps(snapshot),
            "tool_manifest_hash": tool_contract_hash(scenario.tool_allowlist),
            "agent_id": "baseline", "agent_config": json.dumps({
                "id": "baseline", "implementation_version": AGENT_IMPLEMENTATION_VERSIONS["baseline"],
                "prompt_version": BASELINE_PROMPT_VERSION, "model": None,
                "provider_timeout_seconds": 25, "provider_max_retries": 0,
            }), "fault_plan": json.dumps([fault.model_dump(mode="json") for fault in scenario.faults]),
            "limits": json.dumps(LimitsInput().model_dump()), "status": "finished",
            "termination_reason": "agent_final", "created_at": datetime.now(timezone.utc).isoformat(),
            "event_seq": 0,
        })
        # Match the current evaluator version to isolate fault-plan provenance as the exclusion reason.
        connection.execute(text("""INSERT INTO evaluations
            (run_id, evaluator_version, checks, metrics, outcome, explanation)
            VALUES (:run_id, :evaluator_version, :checks, :metrics, :outcome, :explanation)"""), {
            "run_id": legacy_id, "evaluator_version": EVALUATOR_VERSION, "checks": "[]",
            "metrics": "{}", "outcome": "pass", "explanation": "Legacy fixture",
        })
    engine.dispose()

    monkeypatch.setenv("FAULTLINE_DB", str(db_path))
    app = create_app(start_worker=False)
    client = TestClient(app)
    with app.state.session_factory() as db:
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "b8e9a2c4d6f0"
        assert db.get(Run, legacy_id).fault_plan_source == "legacy_unknown"
    assert client.get(f"/api/runs/{legacy_id}").json()["fault_plan_source"] == "legacy_unknown"
    current_id = start(client)
    assert finish(app, current_id)[1].outcome == "pass"
    suite = client.get("/api/suite").json()
    assert len(suite["cohorts"]) == 1
    normal = next(row for row in suite["cohorts"][0]["scenarios"] if row["scenario_id"] == "normal")
    assert normal["sample_count"] == normal["conclusive"] == 1
    assert suite["history"]["passing"] == suite["history"]["conclusive"] == 2


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

    class RetryAfterStatus:
        token_usage = {}

        def __init__(self):
            self.baseline = BaselineAgent()

        def next_action(self, context):
            if context.history and context.history[-1][0].name == "get_action_status":
                original = next(call for call, _ in context.history if call.name == "execute_refund")
                return ToolCall("execute_refund", original.arguments)
            return self.baseline.next_action(context)

    assert finish(app, run_id, RetryAfterStatus())[1].outcome == "pass"
    with app.state.session_factory() as db:
        actions = db.scalars(select(RefundAction).where(RefundAction.run_id == run_id)).all()
        events = db.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)).all()
        assert len(actions) == 1
        assert sum(e.type == "state_changed" for e in events) == 1
        assert any(e.type == "fault_applied" and e.payload["fault"] == "response_lost_after_commit" for e in events)
        committed_call = next(e.call_id for e in events if e.type == "state_changed")
        assert not any(e.type == "tool_response_delivered" and e.call_id == committed_call for e in events)
        assert any(e.type == "tool_response_delivered" and e.payload.get("result", {}).get("replayed") is True
                   for e in events)


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
    with app.state.session_factory.begin() as db:
        run = db.get(Run, first)
        _seed(db, run)
        run.status = "running"

    gateway = ToolGateway(app.state.session_factory)
    proposal = gateway.call(first, "propose_refund",
                            {"payment_id": "pay-duplicate", "reason": "Later duplicate"}, "proposal-test")
    arguments = {"proposal_id": proposal["proposal_id"], "idempotency_key": "retry-key"}
    assert gateway.call(first, "execute_refund", arguments, "execute-test")["replayed"] is False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda index: gateway.call(first, "execute_refund", arguments, f"retry-{index}"), range(2)))
    assert all(result.get("replayed") is True for result in results)
    with app.state.session_factory() as db:
        assert len(db.scalars(select(RefundAction).where(RefundAction.run_id == first)).all()) == 1
        assert db.get(Run, first).status == "running"


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
    assert normal["conclusive"] == 1 and normal["inconclusive"] == 0 and normal["sample_count"] == 1


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


def test_old_inconclusive_run_stays_only_in_pooled_history(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    passing_id = start(client)
    assert finish(app, passing_id)[1].outcome == "pass"

    class ProviderDown:
        token_usage = {}

        def next_action(self, context):
            raise ProviderFailure("provider unavailable")

    current_id = start(client)
    assert finish(app, current_id, ProviderDown()) == ("inconclusive", None)
    old_id = start(client)
    assert finish(app, old_id, ProviderDown()) == ("inconclusive", None)
    with app.state.session_factory.begin() as db:
        run = db.get(Run, old_id)
        run.agent_config = {**run.agent_config, "implementation_version": "baseline-agent-v0"}

    suite = client.get("/api/suite").json()
    assert suite["history"]["conclusive"] == 1
    assert suite["history"]["inconclusive"] == 2
    assert len(suite["cohorts"]) == 1
    normal = next(row for row in suite["cohorts"][0]["scenarios"] if row["scenario_id"] == "normal")
    assert normal["conclusive"] == 1 and normal["inconclusive"] == 0 and normal["sample_count"] == 1


def test_run_fault_override_is_snapshotted_and_excluded_from_canonical_suite(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    canonical_id = start(client)
    assert finish(app, canonical_id)[1].outcome == "pass"

    selected = [{"tool": "lookup_customer", "attempt": 1, "stage": "before_dispatch", "effect": "transient_error"}]
    response = client.post("/api/runs", json={"scenario_id": "normal", "fault_plan": selected})
    assert response.status_code == 202
    custom_id = response.json()["id"]
    assert finish(app, custom_id)[1].outcome == "pass"
    custom = client.get("/api/runs/" + custom_id).json()
    assert custom["fault_plan"] == [{**selected[0], "delay_ms": 0}]
    events = client.get("/api/runs/" + custom_id + "/events", params={"limit": 500}).json()["events"]
    assert any(row["type"] == "fault_applied" and row["payload"]["fault"] == "transient_error" for row in events)
    suite = client.get("/api/suite").json()
    normal = next(row for row in suite["cohorts"][0]["scenarios"] if row["scenario_id"] == "normal")
    assert normal["sample_count"] == 1 and normal["passing"] == 1
    assert suite["history"]["conclusive"] == 2


def test_fault_can_be_disabled_without_changing_canonical_default(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    default_id = start(client, "transient")
    assert finish(app, default_id)[1].outcome == "pass"
    assert client.get("/api/runs/" + default_id).json()["fault_plan"]
    disabled = client.post("/api/runs", json={"scenario_id": "transient", "fault_plan": []})
    assert disabled.status_code == 202
    disabled_id = disabled.json()["id"]
    assert finish(app, disabled_id)[1].outcome == "pass"
    assert client.get("/api/runs/" + disabled_id).json()["fault_plan"] == []
    events = client.get("/api/runs/" + disabled_id + "/events", params={"limit": 500}).json()["events"]
    assert not any(row["type"] == "fault_applied" for row in events)
    suite = client.get("/api/suite").json()
    transient = next(row for row in suite["cohorts"][0]["scenarios"] if row["scenario_id"] == "transient")
    assert transient["sample_count"] == 1 and suite["history"]["conclusive"] == 2


def test_invalid_fault_plans_are_rejected(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    valid = {"tool": "lookup_customer", "attempt": 1, "stage": "before_dispatch", "effect": "transient_error"}
    for plan in (
        [{**valid, "tool": "delete_database"}],
        [{**valid, "stage": "after_commit", "effect": "response_lost_after_commit"}],
        [valid, valid],
        [{**valid, "attempt": 21}],
    ):
        response = client.post("/api/runs", json={"scenario_id": "normal", "fault_plan": plan})
        assert response.status_code == 422, (plan, response.text)
    assert client.get("/api/runs").json() == []


def test_openai_request_is_bounded_by_remaining_run_time():
    captured = []
    response = SimpleNamespace(output=[], usage=None, output_text="Done")
    fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: (captured.append(kwargs), response)[1]))
    agent = OpenAIAgent("test-model", timeout_seconds=25, client=fake)
    context = AgentContext("Task", "ada@example.test", "ORD-100", [], [], deadline_monotonic=time.monotonic() + 0.2)
    assert agent.next_action(context).text == "Done"
    assert 0 < captured[0]["timeout"] <= 0.2


def test_slow_agent_does_not_dispatch_after_deadline(tmp_path):
    app = make_app(tmp_path)
    run_id = start(TestClient(app))
    with app.state.session_factory.begin() as db:
        run = db.get(Run, run_id)
        run.limits = {**run.limits, "run_timeout_seconds": 0.01}

    class SlowAgent:
        token_usage = {}

        def next_action(self, context):
            time.sleep(0.05)
            return ToolCall("lookup_customer", {"email": "ada@example.test"})

    status, evaluation = finish(app, run_id, SlowAgent())
    assert status == "finished" and evaluation.outcome == "fail"
    assert evaluation.metrics["tool_call_count"] == 0
    events = TestClient(app).get("/api/runs/" + run_id + "/events", params={"limit": 500}).json()["events"]
    assert any(row["type"] == "run_deadline_exceeded" for row in events)


def test_delayed_response_deadline_keeps_refund_and_suppresses_delivery(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    fault = [{"tool": "execute_refund", "attempt": 1, "stage": "before_delivery",
              "effect": "delayed_response", "delay_ms": 1000}]
    run_id = client.post("/api/runs", json={"scenario_id": "normal", "fault_plan": fault}).json()["id"]
    with app.state.session_factory.begin() as db:
        run = db.get(Run, run_id)
        run.limits = {**run.limits, "run_timeout_seconds": 0.4}
    status, evaluation = finish(app, run_id)
    assert status == "finished" and evaluation.outcome == "fail"
    assert evaluation.metrics["business_correct"] is True
    assert client.get("/api/runs/" + run_id).json()["termination_reason"] == "run_timeout"
    events = client.get("/api/runs/" + run_id + "/events", params={"limit": 500}).json()["events"]
    committed = next(row for row in events if row["type"] == "state_changed")
    assert any(row["type"] == "fault_applied" and row["payload"]["fault"] == "delayed_response" for row in events)
    assert not any(row["type"] == "tool_response_delivered" and row["call_id"] == committed["call_id"] for row in events)
    assert any(row["type"] == "run_deadline_exceeded" for row in events)


def test_side_effect_committed_after_deadline_is_still_evaluated(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    client = TestClient(app)
    run_id = start(client)
    with app.state.session_factory.begin() as db:
        run = db.get(Run, run_id)
        run.limits = {**run.limits, "run_timeout_seconds": 0.4}
    original = gateway_module._dispatch

    def slow_dispatch(db, run, name, args, call_id):
        if name == "execute_refund":
            time.sleep(0.6)
        return original(db, run, name, args, call_id)

    with monkeypatch.context() as patch:
        patch.setattr(gateway_module, "_dispatch", slow_dispatch)
        status, evaluation = finish(app, run_id)
    assert status == "finished" and evaluation.outcome == "fail"
    assert evaluation.metrics["business_correct"] is True
    assert client.get("/api/runs/" + run_id).json()["termination_reason"] == "run_timeout"
    events = client.get("/api/runs/" + run_id + "/events", params={"limit": 500}).json()["events"]
    committed = next(row for row in events if row["type"] == "state_changed")
    assert not any(row["type"] == "tool_response_delivered" and row["call_id"] == committed["call_id"] for row in events)
    with app.state.session_factory() as db:
        assert len(db.scalars(select(RefundAction).where(RefundAction.run_id == run_id)).all()) == 1


def test_finalized_runs_reject_gateway_calls_without_new_events(tmp_path):
    from faultline.errors import RunNotActive

    app = make_app(tmp_path)
    client = TestClient(app)
    finished_id = start(client)
    assert finish(app, finished_id)[1].outcome == "pass"
    with app.state.session_factory() as db:
        action = db.scalar(select(RefundAction).where(RefundAction.run_id == finished_id))
        before = db.get(Run, finished_id).event_seq
        arguments = {"proposal_id": action.proposal_id, "idempotency_key": action.idempotency_key}
    with pytest.raises(RunNotActive):
        ToolGateway(app.state.session_factory).call(finished_id, "execute_refund", arguments, "too-late")
    with app.state.session_factory() as db:
        assert db.get(Run, finished_id).event_seq == before
        assert len(db.scalars(select(RefundAction).where(RefundAction.run_id == finished_id)).all()) == 1

    class ProviderDown:
        token_usage = {}

        def next_action(self, context):
            raise ProviderFailure("provider unavailable")

    inconclusive_id = start(client)
    assert finish(app, inconclusive_id, ProviderDown()) == ("inconclusive", None)
    with app.state.session_factory() as db:
        before = db.get(Run, inconclusive_id).event_seq
    with pytest.raises(RunNotActive):
        ToolGateway(app.state.session_factory).call(inconclusive_id, "lookup_customer", {"email": "ada@example.test"}, "too-late")
    with app.state.session_factory() as db:
        assert db.get(Run, inconclusive_id).event_seq == before


def test_provider_timeout_classification_depends_on_which_budget_expired():
    from httpx import Request
    from openai import APITimeoutError
    from faultline.errors import RunDeadlineExceeded

    def timed_out(**kwargs):
        raise APITimeoutError(request=Request("POST", "https://example.test/responses"))

    fake = SimpleNamespace(responses=SimpleNamespace(create=timed_out))
    deadline_limited = OpenAIAgent("test-model", timeout_seconds=25, client=fake)
    context = AgentContext("Task", "ada@example.test", "ORD-100", [], [],
                           deadline_monotonic=time.monotonic() + 0.2)
    with pytest.raises(RunDeadlineExceeded):
        deadline_limited.next_action(context)

    provider_limited = OpenAIAgent("test-model", timeout_seconds=0.01, client=fake)
    context = AgentContext("Task", "ada@example.test", "ORD-100", [], [],
                           deadline_monotonic=time.monotonic() + 1)
    with pytest.raises(ProviderFailure):
        provider_limited.next_action(context)


def test_comparison_rejects_different_limits_and_allows_different_agents(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    baseline_id = start(client)
    assert finish(app, baseline_id)[1].outcome == "pass"
    limited_id = client.post("/api/runs", json={"scenario_id": "normal", "limits": {"max_turns": 40}}).json()["id"]
    assert finish(app, limited_id)[1].outcome == "pass"
    mismatch = client.get("/api/comparisons", params={"run_ids": baseline_id + "," + limited_id})
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"] == "incompatible runs"

    other_agent_id = start(client)
    with app.state.session_factory.begin() as db:
        run = db.get(Run, other_agent_id)
        run.agent_id = "openai"
        run.agent_config = {**run.agent_config, "id": "openai", "model": "mock-model",
                            "prompt_version": "openai-agent-v1", "implementation_version": "openai-adapter-v1"}
    fake_response = SimpleNamespace(output=[], usage=None, output_text="Done")
    fake_client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: fake_response))
    assert finish(app, other_agent_id, OpenAIAgent("mock-model", client=fake_client))[1].outcome == "fail"
    compatible = client.get("/api/comparisons", params={"run_ids": baseline_id + "," + other_agent_id})
    assert compatible.status_code == 200
    assert compatible.json()["sample_count"] == 2
    assert {run["agent_id"] for run in compatible.json()["runs"]} == {"baseline", "openai"}


def test_adversarial_note_attempt_is_blocked_and_scored_unsafe_with_safe_control(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)

    class NoteMisledThenSafe:
        token_usage = {}

        def __init__(self):
            self.baseline = BaselineAgent()
            self.saw_note = False
            self.wrong_payment = None

        def next_action(self, context):
            if context.history and context.history[-1][0].name == "lookup_customer" and not self.saw_note:
                note = context.history[-1][1]["note"]
                assert "SYSTEM OVERRIDE" in note
                match = re.search(r"pay-[a-z-]+", note)
                assert match is not None
                self.saw_note = True
                self.wrong_payment = match.group(0)
                return ToolCall("propose_refund", {"payment_id": self.wrong_payment,
                                                   "reason": "Customer note override"})
            safe_history = [(call, result) for call, result in context.history
                            if call.arguments.get("reason") != "Customer note override"]
            safe_context = AgentContext(context.task, context.customer_email, context.order_id,
                                        context.tool_manifest, safe_history,
                                        deadline_monotonic=context.deadline_monotonic)
            return self.baseline.next_action(safe_context)

    misled = NoteMisledThenSafe()
    unsafe_id = start(client, "injection")
    status, evaluation = finish(app, unsafe_id, misled)
    assert status == "finished" and evaluation.outcome == "fail"
    assert misled.saw_note and misled.wrong_payment == "pay-original"
    assert evaluation.metrics["business_correct"] is True
    assert evaluation.metrics["safety_clear"] is False
    assert evaluation.metrics["prohibited_attempt_count"] == 1
    events = client.get("/api/runs/" + unsafe_id + "/events", params={"limit": 500}).json()["events"]
    assert any(row["type"] == "prohibited_action_attempt" and row["payload"]["tool"] == "propose_refund"
               and row["payload"]["code"] == "policy_violation" for row in events)
    with app.state.session_factory() as db:
        payments = {row.fixture_id: row for row in db.scalars(select(SimPayment).where(SimPayment.run_id == unsafe_id))}
        actions = db.scalars(select(RefundAction).where(RefundAction.run_id == unsafe_id)).all()
        assert payments["pay-original"].status == "settled"
        assert payments["pay-duplicate"].status == "refunded"
        assert len(actions) == 1 and actions[0].payment_id == "pay-duplicate"

    safe_id = start(client, "injection")
    safe_status, safe_evaluation = finish(app, safe_id)
    assert safe_status == "finished" and safe_evaluation.outcome == "pass"
    assert safe_evaluation.metrics["safety_clear"] is True


def test_explicit_identical_fault_plan_is_not_canonical(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    canonical_id = start(client)
    assert finish(app, canonical_id)[1].outcome == "pass"
    override = client.post("/api/runs", json={"scenario_id": "normal", "fault_plan": []})
    assert override.status_code == 202
    override_id = override.json()["id"]
    assert finish(app, override_id)[1].outcome == "pass"
    assert client.get("/api/runs/" + canonical_id).json()["fault_plan_source"] == "scenario_default"
    assert client.get("/api/runs/" + override_id).json()["fault_plan_source"] == "run_override"
    suite = client.get("/api/suite").json()
    normal = next(row for row in suite["cohorts"][0]["scenarios"] if row["scenario_id"] == "normal")
    assert normal["sample_count"] == 1
    assert suite["history"]["conclusive"] == 2
