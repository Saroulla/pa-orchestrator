"""State dataclasses live here."""
from dataclasses import dataclass, field


@dataclass(slots=True)
class IterationState:
    iteration: int
    decided_action: str
    stdout: str
    stderr: str
    exit_code: int
    analyses: list[str] = field(default_factory=list)
    synthesis: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0


@dataclass(slots=True)
class GoalState:
    goal: str
    session_id: str
    iterations: list[IterationState] = field(default_factory=list)
    achieved: bool = False
    final_summary: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0
