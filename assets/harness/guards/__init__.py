"""
Anti-faking guardrails. Each guard makes one class of fake result structurally
impossible, or loudly detectable. Every guard ships with a meta-test that feeds
it a deliberate fake and asserts the guard rejects it (G8).
"""

class GuardError(Exception):
    """Base for every guard failure. Guards RAISE. They never return a silent
    default, because a silent default is how fake results slip through."""


class DataIntegrityError(GuardError):
    pass


class LeakageError(GuardError):
    pass


class SanityError(GuardError):
    pass


class AuditError(GuardError):
    pass


class ReproducibilityError(GuardError):
    pass


class FabricationError(GuardError):
    pass


__all__ = [
    "GuardError", "DataIntegrityError", "LeakageError", "SanityError",
    "AuditError", "ReproducibilityError", "FabricationError",
]
