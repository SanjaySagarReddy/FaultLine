from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from alembic import command
from alembic.config import Config

from .agents import AGENT_IMPLEMENTATION_VERSIONS, BASELINE_PROMPT_VERSION, OpenAIAgent, configured_agents
from .contracts import FaultRule, canonical_hash, load_scenarios, tool_manifest, tool_contract_hash
from .evaluation import EVALUATOR_VERSION
from .models import Base, Evaluation, Run, RunEvent, make_session_factory
from .runner import RunWorker
from .suite import build_suite


class LimitsInput(BaseModel):
    max_turns: int = Field(default=12, ge=1, le=40)
    max_tool_calls: int = Field(default=20, ge=1, le=60)
    run_timeout_seconds: int = Field(default=120, ge=10, le=600)
    provider_timeout_seconds: int = Field(default=25, ge=5, le=120)


class CreateRunInput(BaseModel):
    scenario_id: str
    agent_id: str = "baseline"
    model: str | None = Field(default=None, max_length=100)
    fault_plan: list[FaultRule] | None = None
    limits: LimitsInput = Field(default_factory=LimitsInput)


def _utc_iso(value: datetime) -> str:
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def _event_json(event: RunEvent) -> dict:
    return {"run_id": event.run_id, "seq": event.seq, "timestamp_utc": _utc_iso(event.timestamp_utc),
            "type": event.type, "source": event.source, "call_id": event.call_id, "payload": event.payload}


def _evaluation_json(evaluation: Evaluation | None) -> dict | None:
    if evaluation is None:
        return None
    return {"run_id": evaluation.run_id, "evaluator_version": evaluation.evaluator_version,
            "checks": evaluation.checks, "metrics": evaluation.metrics, "outcome": evaluation.outcome,
            "explanation": evaluation.explanation}


def _run_json(run: Run, evaluation: Evaluation | None = None) -> dict:
    return {"id": run.id, "scenario_id": run.scenario_id, "scenario_version": run.scenario_version,
            "scenario_hash": run.scenario_hash, "tool_manifest_hash": run.tool_manifest_hash,
            "agent_id": run.agent_id, "agent_config": run.agent_config, "fault_plan": run.fault_plan,
            "limits": run.limits, "status": run.status, "termination_reason": run.termination_reason,
            "final_answer": run.final_answer, "created_at": _utc_iso(run.created_at),
            "started_at": _utc_iso(run.started_at) if run.started_at else None,
            "finished_at": _utc_iso(run.finished_at) if run.finished_at else None,
            "evaluation": _evaluation_json(evaluation)}


def create_app(database_url: str | None = None, start_worker: bool = True) -> FastAPI:
    use_migrations = database_url is None
    if database_url is None:
        db_path = Path(os.getenv("FAULTLINE_DB", "faultline.db")).resolve()
        database_url = f"sqlite:///{db_path.as_posix()}"
    session_factory = make_session_factory(database_url)
    if use_migrations:
        backend_dir = Path(__file__).resolve().parent.parent
        config = Config(str(backend_dir / "alembic.ini"))
        config.set_main_option("script_location", str(backend_dir / "migrations"))
        config.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(config, "head")
    else:
        Base.metadata.create_all(session_factory.kw["bind"])
    scenarios = load_scenarios()
    worker = RunWorker(session_factory)

    @asynccontextmanager
    async def lifespan(_app):
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()

    app = FastAPI(title="Faultline", version="1.0.0", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.scenarios = scenarios
    app.state.worker = worker
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/scenarios")
    def list_scenarios():
        return [{**scenario.public(), "content_hash": canonical_hash(scenario.model_dump(mode="json"))}
                for scenario in scenarios.values()]

    @app.get("/api/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str):
        scenario = scenarios.get(scenario_id)
        if scenario is None:
            raise HTTPException(404, "scenario not found")
        return {**scenario.public(), "content_hash": canonical_hash(scenario.model_dump(mode="json")),
                "tools": tool_manifest(scenario.tool_allowlist)}

    @app.get("/api/agents")
    def get_agents():
        return configured_agents()

    @app.post("/api/runs", status_code=202)
    def create_run(payload: CreateRunInput):
        scenario = scenarios.get(payload.scenario_id)
        if scenario is None:
            raise HTTPException(422, "unknown scenario")
        if payload.agent_id not in {"baseline", "openai"}:
            raise HTTPException(422, "unknown agent")
        if payload.agent_id == "openai" and not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(422, "OpenAI agent is not configured")
        selected_faults = scenario.faults if payload.fault_plan is None else payload.fault_plan
        if (len(selected_faults) > payload.limits.max_tool_calls
                or len({(rule.tool, rule.attempt) for rule in selected_faults}) != len(selected_faults)
                or any(rule.tool not in scenario.tool_allowlist or rule.attempt > payload.limits.max_tool_calls
                       for rule in selected_faults)):
            raise HTTPException(422, "fault plan uses an unavailable tool, duplicate attempt, or unreachable attempt")
        snapshot = scenario.model_dump(mode="json")
        agent_config = {"id": payload.agent_id,
                        "implementation_version": AGENT_IMPLEMENTATION_VERSIONS[payload.agent_id],
                        "prompt_version": OpenAIAgent.PROMPT_VERSION if payload.agent_id == "openai" else BASELINE_PROMPT_VERSION,
                        "model": payload.model or os.getenv("OPENAI_MODEL", "gpt-5.6-terra") if payload.agent_id == "openai" else None,
                        "provider_timeout_seconds": payload.limits.provider_timeout_seconds,
                        "provider_max_retries": 0}
        run = Run(id=str(uuid.uuid4()), scenario_id=scenario.id, scenario_version=scenario.version,
                  scenario_hash=canonical_hash(snapshot), scenario_snapshot=snapshot,
                  tool_manifest_hash=tool_contract_hash(scenario.tool_allowlist),
                  agent_id=payload.agent_id, agent_config=agent_config,
                  fault_plan=[fault.model_dump(mode="json") for fault in selected_faults],
                  limits=payload.limits.model_dump(), status="queued")
        with session_factory.begin() as db:
            db.add(run)
        return {"id": run.id, "status": "queued"}

    @app.get("/api/runs")
    def list_runs(scenario_id: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
        with session_factory() as db:
            query = select(Run).order_by(Run.created_at.desc(), Run.id.desc()).limit(limit)
            if scenario_id:
                query = query.where(Run.scenario_id == scenario_id)
            runs = db.scalars(query).all()
            evaluations = {e.run_id: e for e in db.scalars(select(Evaluation).where(Evaluation.run_id.in_([r.id for r in runs]))).all()} if runs else {}
            return [_run_json(run, evaluations.get(run.id)) for run in runs]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        with session_factory() as db:
            run = db.get(Run, run_id)
            if run is None:
                raise HTTPException(404, "run not found")
            return _run_json(run, db.get(Evaluation, run_id))

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str, after_seq: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500)):
        with session_factory() as db:
            if db.get(Run, run_id) is None:
                raise HTTPException(404, "run not found")
            events = db.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
                                .order_by(RunEvent.seq).limit(limit)).all()
            return {"events": [_event_json(e) for e in events], "next_after_seq": events[-1].seq if events else after_seq}

    @app.get("/api/runs/{run_id}/evaluation")
    def get_evaluation(run_id: str):
        with session_factory() as db:
            evaluation = db.get(Evaluation, run_id)
            if evaluation is None:
                raise HTTPException(404, "evaluation not available")
            return _evaluation_json(evaluation)

    @app.get("/api/comparisons")
    def compare(run_ids: str):
        ids = [value.strip() for value in run_ids.split(",") if value.strip()]
        if len(ids) < 2 or len(ids) > 8 or len(ids) != len(set(ids)):
            raise HTTPException(422, "provide 2 to 8 distinct run IDs")
        with session_factory() as db:
            runs = [db.get(Run, run_id) for run_id in ids]
            if any(run is None for run in runs):
                raise HTTPException(404, "run not found")
            evaluations = [db.get(Evaluation, run.id) for run in runs]
            if any(e is None for e in evaluations):
                raise HTTPException(422, "runs must be conclusive")
            keys = {(r.scenario_hash, r.tool_manifest_hash, canonical_hash(r.fault_plan), e.evaluator_version)
                    for r, e in zip(runs, evaluations)}
            if len(keys) != 1:
                raise HTTPException(422, "incompatible runs")
            return {"compatible": True, "sample_count": len(runs), "runs": [_run_json(r, e) for r, e in zip(runs, evaluations)]}

    @app.get("/api/suite")
    def suite():
        with session_factory() as db:
            runs = db.scalars(select(Run)).all()
            evaluations = db.scalars(select(Evaluation)).all()
            return build_suite(scenarios, runs, evaluations)

    return app


app = create_app()
