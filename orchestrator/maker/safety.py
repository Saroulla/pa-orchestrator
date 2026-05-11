class MAKERError(Exception):
    """Base for all MAKER-raised errors."""


class MAKERSafetyError(MAKERError):
    """Refusal to execute a PowerShell action that fails policy checks."""


class MAKERTimeoutError(MAKERError):
    """PowerShell process exceeded its deadline; kill chain applied."""


class MAKERMaxIterationsError(MAKERError):
    """Loop hit the iteration cap without achieving the goal."""
