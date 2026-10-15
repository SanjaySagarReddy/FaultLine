from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from sqlalchemy import select, update

from .agents import AgentContext, FinalAnswer, ToolCall, create_agent
from .contracts import tool_manifest
from .evaluation import evaluate
from .errors import InfrastructureFailure, ProviderFailure
from .gateway import ToolGateway, append_event, sanitize_payload
from .models import Run, SimCustomer, SimPayment


def _seed(db, run: Run):
    for customer in run.scenario_snapshot["customers"]:
        db.add(SimCustomer(run_id=run.id, fixture_id=customer["id"], email=customer["email"], note=customer.get("note", "")))
    for payment in run.scenario_snapshot["payments"]:
        db.add(SimPayment(run_id=run.id, fixture_id=payment["id"], customer_id=payment["customer_id"],
                          order_id=payment["order_id"], amount_minor=payment["amount_minor"], currency=payment["currency"],
                          status=payment["status"], created_at=datetime.fromisoformat(payment["created_at"].replace("Z", "+00:00"))))


def execute_run(run_id: str, session_factory, agent_override=None):
    gateway = ToolGateway(session_factory)
    with session_factory.begin() as db:
        run = db.get(Run, run_id)
        if run.status not in {"queued", "running"}:
            return
        if run.started_at is None:
            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            _seed(db, run)
            append_event(db, run, "run_started", "runner", {"scenario_id": run.scenario_id, "agent_id": run.agent_id})
        scenario = run.scenario_snapshot
        config = dict(run.agent_config)
        limits = dict(run.limits)

    try:
        agent = agent_override or create_agent(config["id"], config)
    except (ProviderFailure, InfrastructureFailure) as exc:
        _inconclusive(run_id, session_factory, f"adapter_start_failed: {type(exc).__name__}")
        return
    except Exception as exc:
        _agent_error(run_id, session_factory, "agent_execution_error", exc)
        _finish_conclusive(run_id, session_factory, "agent_execution_error", None, {})
        return
    history: list[tuple[ToolCall, dict]] = []
    context = AgentContext(task=scenario["task"], customer_email=scenario["customer_email"],
                           order_id=scenario["order_id"], tool_manifest=tool_manifest(scenario["tool_allowlist"]), history=history)
    deadline = time.monotonic() + limits["run_timeout_seconds"]
    termination = "agent_final"
    final_text = None
    calls = 0
    for turn in range(limits["max_turns"]):
        if time.monotonic() >= deadline:
            termination = "run_timeout"
            break
        try:
            action = agent.next_action(context)
        except (ProviderFailure, InfrastructureFailure) as exc:
            _inconclusive(run_id, session_factory, f"provider_or_infrastructure_error: {type(exc).__name__}")
            return
        except Exception as exc:
            _agent_error(run_id, session_factory, "agent_execution_error", exc)
            termination = "agent_execution_error"
            break
        if time.monotonic() >= deadline:
            termination = "run_timeout"
            break
        if isinstance(action, FinalAnswer):
            if not isinstance(action.text, str):
                _agent_error(run_id, session_factory, "agent_protocol_error", TypeError("final text must be a string"))
                termination = "agent_protocol_error"
                break
            final_text = sanitize_payload(action.text[:4000])
            try:
                with session_factory.begin() as db:
                    run = db.get(Run, run_id)
                    append_event(db, run, "agent_final", "agent", {"text": final_text})
            except Exception as exc:
                _inconclusive(run_id, session_factory, f"event_storage_error: {type(exc).__name__}")
                return
            break
        if (not isinstance(action, ToolCall) or not isinstance(action.name, str)
                or not isinstance(action.arguments, dict)
                or (action.call_id is not None and not isinstance(action.call_id, str))):
            _agent_error(run_id, session_factory, "agent_protocol_error", TypeError("adapter returned invalid action"))
            termination = "agent_protocol_error"
            break
        if calls >= limits["max_tool_calls"]:
            termination = "tool_call_limit"
            break
        calls += 1
        call_id = action.call_id or f"call-{calls}"
        try:
            observation = gateway.call(run_id, action.name, action.arguments, call_id)
        except InfrastructureFailure as exc:
            _inconclusive(run_id, session_factory, f"infrastructure_error: {type(exc).__name__}")
            return
        history.append((action, observation))
    else:
        termination = "turn_limit"

    _finish_conclusive(run_id, session_factory, termination, final_text, getattr(agent, "token_usage", {}))


def _agent_error(run_id: str, session_factory, reason: str, exc: Exception):
    with session_factory.begin() as db:
        run = db.get(Run, run_id)
        append_event(db, run, "agent_error", "runner", {"reason": reason, "error_type": type(exc).__name__})


def _finish_conclusive(run_id: str, session_factory, termination: str, final_text: str | None, token_usage: dict):
    try:
        with session_factory.begin() as db:
            run = db.get(Run, run_id)
            run.status = "evaluating"
            run.termination_reason = termination
            run.final_answer = final_text
            run.agent_config = {**run.agent_config, "token_usage": token_usage}
        with session_factory.begin() as db:
            run = db.get(Run, run_id)
            evaluation = evaluate(db, run, termination)
            run.status = "finished"
            run.finished_at = datetime.now(timezone.utc)
            append_event(db, run, "run_finished", "runner", {"outcome": evaluation.outcome, "termination_reason": termination})
    except Exception as exc:
        _inconclusive(run_id, session_factory, f"evaluation_error: {type(exc).__name__}")


def _inconclusive(run_id: str, session_factory, reason: str):
    with session_factory.begin() as db:
        run = db.get(Run, run_id)
        run.status = "inconclusive"
        run.termination_reason = reason
        run.finished_at = datetime.now(timezone.utc)
        append_event(db, run, "run_finished", "runner", {"outcome": "inconclusive", "reason": reason})


def reconcile_interrupted(session_factory):
    with session_factory.begin() as db:
        runs = db.scalars(select(Run).where(Run.status.in_(["running", "evaluating"]))).all()
        for run in runs:
            run.status = "inconclusive"
            run.termination_reason = "interrupted_before_completion"
            run.finished_at = datetime.now(timezone.utc)
            append_event(db, run, "run_finished", "worker", {"outcome": "inconclusive", "reason": "interrupted_before_completion"})


class RunWorker:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="faultline-run-worker", daemon=True)

    def start(self):
        reconcile_interrupted(self.session_factory)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _loop(self):
        while not self.stop_event.is_set():
            run_id = None
            with self.session_factory.begin() as db:
                run = db.scalar(select(Run).where(Run.status == "queued").order_by(Run.created_at, Run.id).limit(1))
                if run:
                    changed = db.execute(update(Run).where(Run.id == run.id, Run.status == "queued").values(status="running"))
                    if changed.rowcount:
                        run_id = run.id
            if run_id:
                execute_run(run_id, self.session_factory)
            else:
                self.stop_event.wait(0.2)
