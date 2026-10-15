"""Canonical five-scenario suite aggregation over persisted run snapshots."""

from __future__ import annotations

from .contracts import canonical_hash, tool_contract_hash
from .evaluation import EVALUATOR_VERSION


REQUIRED_AGENT_CONFIG = {
    "id", "implementation_version", "prompt_version", "model",
    "provider_timeout_seconds", "provider_max_retries",
}


def build_suite(scenarios: dict, runs: list, evaluations: list) -> dict:
    conditions = {}
    for scenario_id, scenario in sorted(scenarios.items()):
        conditions[scenario_id] = {
            "scenario_hash": canonical_hash(scenario.model_dump(mode="json")),
            "tool_manifest_hash": tool_contract_hash(scenario.tool_allowlist),
            "fault_plan_hash": canonical_hash([fault.model_dump(mode="json") for fault in scenario.faults]),
        }
    suite_definition_hash = canonical_hash({"scenarios": conditions, "evaluator_version": EVALUATOR_VERSION})
    by_run = {evaluation.run_id: evaluation for evaluation in evaluations}
    passing = sum(evaluation.outcome == "pass" for evaluation in evaluations)
    conclusive = len(evaluations)
    history = {
        "passing": passing,
        "conclusive": conclusive,
        "inconclusive": sum(run.status == "inconclusive" for run in runs),
        "reliability_score": 100 * passing / conclusive if conclusive else None,
    }

    grouped = {}
    for run in runs:
        expected = conditions.get(run.scenario_id)
        # Unevaluated runs have no trustworthy evaluator-version provenance.
        # They remain visible only in pooled history and never enter a score.
        if expected is None or run.status != "finished":
            continue
        if (run.fault_plan_source != "scenario_default"
                or run.scenario_hash != expected["scenario_hash"]
                or run.tool_manifest_hash != expected["tool_manifest_hash"]
                or canonical_hash(run.fault_plan) != expected["fault_plan_hash"]):
            continue
        evaluation = by_run.get(run.id)
        if evaluation is None or evaluation.evaluator_version != EVALUATOR_VERSION:
            continue
        agent_config = {key: value for key, value in run.agent_config.items() if key != "token_usage"}
        if not REQUIRED_AGENT_CONFIG.issubset(agent_config) or agent_config["id"] != run.agent_id:
            continue
        cohort_id = canonical_hash({
            "suite_definition_hash": suite_definition_hash,
            "agent_id": run.agent_id,
            "agent_config": agent_config,
            "limits": run.limits,
        })
        cohort = grouped.setdefault(cohort_id, {
            "cohort_id": cohort_id,
            "agent_id": run.agent_id,
            "agent_config": agent_config,
            "limits": run.limits,
            "runs": [],
        })
        cohort["runs"].append(run)

    cohorts = []
    for cohort in grouped.values():
        scenario_rows = []
        for scenario_id in conditions:
            samples = [run for run in cohort["runs"] if run.scenario_id == scenario_id]
            outcomes = [by_run[run.id].outcome for run in samples if run.id in by_run]
            scenario_passing = outcomes.count("pass")
            scenario_conclusive = len(outcomes)
            scenario_rows.append({
                "scenario_id": scenario_id,
                "passing": scenario_passing,
                "conclusive": scenario_conclusive,
                "inconclusive": sum(run.status == "inconclusive" for run in samples),
                "sample_count": len(samples),
                "pass_rate": 100 * scenario_passing / scenario_conclusive if scenario_conclusive else None,
            })
        completed = sum(row["conclusive"] > 0 for row in scenario_rows)
        cohort["scenario_count"] = len(scenario_rows)
        cohort["completed_scenarios"] = completed
        cohort["suite_score"] = (
            sum(row["pass_rate"] for row in scenario_rows) / len(scenario_rows)
            if completed == len(scenario_rows) and scenario_rows else None
        )
        cohort["passing"] = sum(row["passing"] for row in scenario_rows)
        cohort["conclusive"] = sum(row["conclusive"] for row in scenario_rows)
        cohort["inconclusive"] = sum(row["inconclusive"] for row in scenario_rows)
        cohort["sample_count"] = sum(row["sample_count"] for row in scenario_rows)
        cohort["scenarios"] = scenario_rows
        del cohort["runs"]
        cohorts.append(cohort)

    cohorts.sort(key=lambda row: (
        row["agent_id"], str(row["agent_config"].get("model")),
        row["agent_config"]["prompt_version"], row["cohort_id"],
    ))
    return {
        "scenario_set": list(conditions),
        "scenario_conditions": conditions,
        "suite_definition_hash": suite_definition_hash,
        "evaluator_version": EVALUATOR_VERSION,
        "cohorts": cohorts,
        "history": history,
    }
