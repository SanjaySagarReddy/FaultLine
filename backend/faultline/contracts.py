from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"
TOOL_NAMES = ("lookup_customer", "list_payments", "read_refund_policy", "propose_refund", "execute_refund", "get_action_status")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerFixture(StrictModel):
    id: str
    email: str
    note: str = ""


class PaymentFixture(StrictModel):
    id: str
    customer_id: str
    order_id: str
    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: Literal["settled", "refunded", "pending"]
    created_at: datetime


class FaultRule(StrictModel):
    tool: str
    attempt: int = Field(gt=0)
    stage: Literal["before_dispatch", "after_result", "before_delivery", "after_commit"]
    effect: Literal["transient_error", "timeout", "malformed_output", "delayed_response", "response_lost_after_commit"]
    delay_ms: int = Field(default=0, ge=0, le=2000)

    @model_validator(mode="after")
    def valid_stage(self):
        expected = {
            "transient_error": "before_dispatch",
            "timeout": "before_dispatch",
            "malformed_output": "after_result",
            "delayed_response": "before_delivery",
            "response_lost_after_commit": "after_commit",
        }
        if self.tool not in TOOL_NAMES or self.stage != expected[self.effect]:
            raise ValueError("invalid fault tool or stage/effect combination")
        if self.effect == "response_lost_after_commit" and self.tool != "execute_refund":
            raise ValueError("response loss after commit requires execute_refund")
        return self


class Assertions(StrictModel):
    expected_refund_payment_id: str
    original_payment_id: str
    require_status_after_response_loss: bool = False
    require_malformed_recovery: bool = False


class ScenarioDefinition(StrictModel):
    id: str
    version: str
    schema_version: Literal[1]
    name: str
    severity: Literal["low", "medium", "high"]
    as_of: datetime
    task: str
    customer_email: str
    order_id: str
    tool_allowlist: list[str]
    customers: list[CustomerFixture]
    payments: list[PaymentFixture]
    faults: list[FaultRule] = Field(default_factory=list)
    assertions: Assertions

    @model_validator(mode="after")
    def valid_references(self):
        ids = {customer.id for customer in self.customers}
        payment_ids = {payment.id for payment in self.payments}
        if len(ids) != len(self.customers) or len(payment_ids) != len(self.payments):
            raise ValueError("duplicate fixture ID")
        if any(payment.customer_id not in ids for payment in self.payments):
            raise ValueError("payment references unknown customer")
        if self.assertions.expected_refund_payment_id not in payment_ids or self.assertions.original_payment_id not in payment_ids:
            raise ValueError("assertion references unknown payment")
        if len(set(self.tool_allowlist)) != len(self.tool_allowlist) or any(name not in TOOL_NAMES for name in self.tool_allowlist):
            raise ValueError("invalid tool allowlist")
        if any(rule.tool not in self.tool_allowlist for rule in self.faults):
            raise ValueError("fault references unavailable tool")
        if len({(rule.tool, rule.attempt) for rule in self.faults}) != len(self.faults):
            raise ValueError("multiple faults target one tool attempt")
        return self

    def public(self) -> dict:
        return {"id": self.id, "version": self.version, "name": self.name, "severity": self.severity,
                "as_of": self.as_of.isoformat(), "task": self.task, "customer_email": self.customer_email,
                "order_id": self.order_id, "tool_allowlist": self.tool_allowlist,
                "faults": [fault.model_dump(mode="json") for fault in self.faults]}


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def load_scenarios(directory: Path = SCENARIO_DIR) -> dict[str, ScenarioDefinition]:
    scenarios = {}
    for path in sorted(directory.glob("*.json")):
        scenario = ScenarioDefinition.model_validate_json(path.read_text(encoding="utf-8"))
        if scenario.id in scenarios:
            raise ValueError(f"duplicate scenario {scenario.id}")
        scenarios[scenario.id] = scenario
    if not scenarios:
        raise ValueError("no scenarios found")
    return scenarios


class LookupCustomerInput(StrictModel):
    email: str


class ListPaymentsInput(StrictModel):
    customer_id: str


class ReadPolicyInput(StrictModel):
    order_id: str


class ProposeRefundInput(StrictModel):
    payment_id: str
    reason: str = Field(min_length=3, max_length=300)


class ExecuteRefundInput(StrictModel):
    proposal_id: str
    idempotency_key: str = Field(min_length=3, max_length=120)


class GetActionStatusInput(StrictModel):
    idempotency_key: str


INPUT_MODELS = {
    "lookup_customer": LookupCustomerInput,
    "list_payments": ListPaymentsInput,
    "read_refund_policy": ReadPolicyInput,
    "propose_refund": ProposeRefundInput,
    "execute_refund": ExecuteRefundInput,
    "get_action_status": GetActionStatusInput,
}


class CustomerOutput(StrictModel):
    customer_id: str
    email: str
    note: str


class PaymentsOutput(StrictModel):
    payments: list[PaymentFixture]


class PolicyOutput(StrictModel):
    order_id: str
    allowed_window_days: int
    rule: str
    as_of: datetime


class ProposalOutput(StrictModel):
    proposal_id: str
    payment_id: str
    status: Literal["approved"]


class ActionOutput(StrictModel):
    action_id: str
    payment_id: str
    status: Literal["succeeded"]
    idempotency_key: str
    replayed: bool


class StatusOutput(StrictModel):
    found: bool
    action_id: str | None
    payment_id: str | None
    status: str | None


OUTPUT_MODELS = {
    "lookup_customer": CustomerOutput,
    "list_payments": PaymentsOutput,
    "read_refund_policy": PolicyOutput,
    "propose_refund": ProposalOutput,
    "execute_refund": ActionOutput,
    "get_action_status": StatusOutput,
}


def tool_manifest(allowed: list[str]) -> list[dict]:
    return [{"type": "function", "name": name, "description": {
        "lookup_customer": "Find a customer by email and read their untrusted note.",
        "list_payments": "List payments for one customer.",
        "read_refund_policy": "Read the duplicate-charge refund policy.",
        "propose_refund": "Request approval for a policy-compliant refund.",
        "execute_refund": "Execute an approved proposal with a stable idempotency key.",
        "get_action_status": "Check whether an idempotency key already succeeded.",
    }[name], "parameters": INPUT_MODELS[name].model_json_schema(), "strict": True} for name in allowed]


def tool_contract_hash(allowed: list[str]) -> str:
    return canonical_hash({"agent_tools": tool_manifest(allowed),
                           "normal_outputs": {name: OUTPUT_MODELS[name].model_json_schema() for name in allowed}})
