class ProviderFailure(Exception):
    """A provider request failed before the agent could make a decision."""


class InfrastructureFailure(Exception):
    """The benchmark infrastructure could not execute or record an action."""
