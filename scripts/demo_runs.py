"""Run the four deterministic release-demo cases in a disposable database."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


class UnsafeThenBaseline:
    token_usage = {}

    def __init__(self):
        from faultline.agents import BaselineAgent

        self.baseline = BaselineAgent()
        self.attempted = False

    def next_action(self, context):
        from faultline.agents import AgentContext, ToolCall

        if not self.attempted:
            self.attempted = True
            return ToolCall("delete_database", {})
        safe_history = [step for step in context.history if step[0].name != "delete_database"]
        return self.baseline.next_action(AgentContext(
            context.task, context.customer_email, context.order_id,
            context.tool_manifest, safe_history, context.deadline_monotonic,
        ))


def main():
    with TemporaryDirectory(prefix="faultline-demo-") as directory:
        # api.py creates its module-level app on import. Point that import at
        # this temporary database before importing it.
        os.environ["FAULTLINE_DB"] = str(Path(directory) / "demo.db")
        from fastapi.testclient import TestClient
        from faultline import api as api_module
        from faultline.runner import execute_run

        app = api_module.create_app(f"sqlite:///{(Path(directory) / 'runs.db').as_posix()}", start_worker=False)
        client = TestClient(app)

        def run_case(label, scenario, agent=None):
            created = client.post("/api/runs", json={"scenario_id": scenario, "agent_id": "baseline"})
            created.raise_for_status()
            run_id = created.json()["id"]
            execute_run(run_id, app.state.session_factory, agent_override=agent)
            run = client.get(f"/api/runs/{run_id}").json()
            events = client.get(f"/api/runs/{run_id}/events").json()["events"]
            evaluation = run["evaluation"]
            assert run["status"] == "finished" and evaluation is not None
            print(f"{label}: {run_id}  outcome={evaluation['outcome']}  "
                  f"business={evaluation['metrics']['business_correct']}  "
                  f"safety={evaluation['metrics']['safety_clear']}  "
                  f"recovery={evaluation['metrics']['behavior_checks_pass']}")
            print("  trace:", ", ".join(event["type"] for event in events
                                        if event["type"] in {"fault_applied", "state_changed", "prohibited_action_attempt"}))
            return run_id

        first = run_case("normal", "normal")
        run_case("response loss", "response_loss")
        run_case("unsafe attempt", "normal", UnsafeThenBaseline())
        second = run_case("normal repeat", "normal")
        comparison = client.get("/api/comparisons", params={"run_ids": f"{first},{second}"})
        comparison.raise_for_status()
        print(f"comparison: compatible={comparison.json()['compatible']}  "
              f"sample_count={comparison.json()['sample_count']}")
        client.close()
        app.state.session_factory.kw["bind"].dispose()
        api_module.app.state.session_factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
