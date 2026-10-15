class ProviderFailure(Exception):
    """A provider request failed before the agent could make a decision."""


class InfrastructureFailure(Exception):
    """The benchmark infrastructure could not execute or record an action."""


class RunDeadlineExceeded(Exception):
    """The run can no longer start or deliver another agent/tool action."""

    def __init__(self, phase: str):
        super().__init__(phase)
        self.phase = phase


class RunNotActive(Exception):
    """A tool call targeted a run outside its executing state."""
