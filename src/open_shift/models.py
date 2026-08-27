from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


DAY_MINUTES = 24 * 60


class ActionType(str, Enum):
    TRAVEL = "travel"
    WORK = "work"
    REST = "rest"
    MESSAGE = "message"
    VISIT_BAR = "visit_bar"
    TALK = "talk"
    INVITE = "invite"
    PROMISE = "promise"


class GoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class AgentState:
    agent_id: str
    display_name: str
    location: str
    money: int
    fatigue: float
    mood: str
    daily_wake_minute: int


@dataclass(slots=True)
class Relationship:
    source_id: str
    target_id: str
    trust: float = 0.0
    warmth: float = 0.0
    debt: int = 0


@dataclass(slots=True)
class Goal:
    goal_id: str
    agent_id: str
    kind: str
    target_id: str | None
    target_value: float
    priority: float
    status: GoalStatus = GoalStatus.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ActionProposal:
    action_type: ActionType
    target_id: str | None = None
    location: str | None = None
    duration_minutes: int = 0
    amount: int = 0
    reason_code: str = "unspecified"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Memory:
    memory_id: int
    event_id: int
    tick: int
    importance: float
    summary: str
    tags: tuple[str, ...]
    source_type: str = "direct"
    confidence: float = 0.8
    visibility: str = "private"
    archived: bool = False
    canonical_key: str | None = None


@dataclass(frozen=True, slots=True)
class Invitation:
    invitation_id: int
    inviter_id: str
    invitee_id: str
    location: str
    proposed_tick: int
    status: str
    created_event_id: int


@dataclass(frozen=True, slots=True)
class Commitment:
    commitment_id: int
    actor_id: str
    target_id: str
    due_tick: int
    status: str
    created_event_id: int
    resolved_event_id: int | None = None


@dataclass(frozen=True, slots=True)
class StoryArc:
    arc_id: int
    owner_id: str
    target_id: str | None
    kind: str
    status: str
    progress: int
    required_progress: int
    created_tick: int
    resolved_tick: int | None = None


@dataclass(frozen=True, slots=True)
class DecisionContext:
    tick: int
    seed: int
    actor: AgentState
    agents: tuple[AgentState, ...]
    relationships: tuple[Relationship, ...]
    goals: tuple[Goal, ...]
    locations: tuple[str, ...]
    memories: tuple[Memory, ...] = ()
    invitations: tuple[Invitation, ...] = ()
    commitments: tuple[Commitment, ...] = ()
    story_arcs: tuple[StoryArc, ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    schedule_id: int
    tick: int
    event_type: str
    actor_id: str | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ActionResult:
    accepted: bool
    reason: str
    event_id: int | None = None
