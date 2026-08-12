from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from .models import (
    ActionProposal,
    ActionType,
    DAY_MINUTES,
    DecisionContext,
)
from .providers import ModelProvider
from .rules import RuleEngine
from .store import WorldStore


ACTION_INTERVAL_MINUTES = 6 * 60


@dataclass(frozen=True, slots=True)
class SimulationReport:
    current_tick: int
    elapsed_days: float
    processed_turns: int
    rejected_actions: int
    provider_errors: int
    event_counts: dict[str, int]
    agents: list[dict[str, Any]]
    completed_goals: list[str]
    memory_count: int
    resolved_story_arcs: int
    autonomous_event_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SimulationEngine:
    def __init__(
        self,
        store: WorldStore,
        provider: ModelProvider,
        *,
        seed: int = 7,
        locations: tuple[str, ...] = ("home", "work", "va11_hall_a"),
    ) -> None:
        self.store = store
        self.provider = provider
        self.seed = seed
        self.locations = locations
        self.rules = RuleEngine(store, locations)
        stored_seed = self.store.get_meta("world_seed")
        if stored_seed is None:
            self.store.set_meta("world_seed", seed)
        elif int(stored_seed) != seed:
            raise ValueError(
                f"world seed mismatch: database={stored_seed}, requested={seed}"
            )

    def bootstrap_schedule(self) -> None:
        if self.store.scheduled_count() > 0:
            return
        now = self.store.current_tick
        day_start = (now // DAY_MINUTES) * DAY_MINUTES
        for agent in self.store.list_agents():
            tick = day_start + agent.daily_wake_minute
            if tick <= now:
                tick += DAY_MINUTES
            self.store.schedule_event(tick, "agent_turn", agent.agent_id)

    def _context(self, tick: int, actor_id: str) -> DecisionContext:
        actor = self.store.get_agent(actor_id)
        if actor is None:
            raise KeyError(f"unknown actor: {actor_id}")
        relationships = tuple(self.store.list_relationships(actor_id))
        goals = tuple(self.store.list_goals(actor_id))
        memory_tags = {
            actor.location,
            *(relationship.target_id for relationship in relationships),
            *(goal.kind for goal in goals),
            *(goal.target_id for goal in goals if goal.target_id is not None),
        }
        return DecisionContext(
            tick=tick,
            seed=self.seed,
            actor=actor,
            agents=tuple(self.store.list_agents()),
            relationships=relationships,
            goals=goals,
            locations=self.locations,
            memories=tuple(
                self.store.retrieve_memories(actor_id, tick, tags=memory_tags)
            ),
            invitations=tuple(self.store.list_invitations(actor_id, "pending")),
            commitments=tuple(self.store.list_commitments(actor_id, "pending")),
            story_arcs=tuple(self.store.list_story_arcs(actor_id, "active")),
        )

    @staticmethod
    def _fallback(context: DecisionContext) -> ActionProposal:
        if context.actor.fatigue >= 0.5:
            return ActionProposal(
                action_type=ActionType.REST,
                duration_minutes=180,
                reason_code="provider_error_fallback",
            )
        return ActionProposal(
            action_type=ActionType.WORK,
            duration_minutes=180,
            reason_code="provider_error_fallback",
            metadata={"wage": 20},
        )

    def run_until(self, target_tick: int) -> SimulationReport:
        if target_tick < self.store.current_tick:
            raise ValueError("target tick cannot be before current world time")
        self.bootstrap_schedule()
        processed_turns = 0
        rejected_actions = 0
        provider_errors = 0

        while True:
            scheduled = self.store.pop_next_scheduled(target_tick)
            if scheduled is None:
                break
            self.store.set_current_tick(scheduled.tick)
            if scheduled.event_type == "invitation_due":
                invitation_id = scheduled.payload.get("invitation_id")
                if isinstance(invitation_id, int):
                    self.rules.resolve_invitation(scheduled.tick, invitation_id)
                continue
            if scheduled.event_type == "commitment_due":
                commitment_id = scheduled.payload.get("commitment_id")
                if isinstance(commitment_id, int):
                    self.rules.resolve_commitment(scheduled.tick, commitment_id)
                continue
            if scheduled.event_type != "agent_turn" or scheduled.actor_id is None:
                self.store.append_event(
                    scheduled.tick,
                    "unknown_scheduled_event",
                    scheduled.actor_id,
                    payload={"event_type": scheduled.event_type},
                )
                continue

            context = self._context(scheduled.tick, scheduled.actor_id)
            try:
                proposal = self.provider.decide(context)
            except Exception as exc:  # provider failure must not stop the world
                provider_errors += 1
                self.store.append_event(
                    scheduled.tick,
                    "provider_error",
                    scheduled.actor_id,
                    payload={"error_type": type(exc).__name__},
                )
                proposal = self._fallback(context)

            result = self.rules.execute(
                scheduled.tick, scheduled.actor_id, proposal
            )
            processed_turns += 1
            if not result.accepted:
                rejected_actions += 1
                self.store.append_event(
                    scheduled.tick,
                    "action_rejected",
                    scheduled.actor_id,
                    proposal.target_id,
                    {
                        "action_type": proposal.action_type.value,
                        "reason": result.reason,
                    },
                )
                fallback = self._fallback(context)
                self.rules.execute(scheduled.tick, scheduled.actor_id, fallback)

            self.store.schedule_event(
                scheduled.tick + ACTION_INTERVAL_MINUTES,
                "agent_turn",
                scheduled.actor_id,
            )

        self.store.set_current_tick(target_tick)
        return self.report(
            processed_turns=processed_turns,
            rejected_actions=rejected_actions,
            provider_errors=provider_errors,
        )

    def run_days(self, days: int) -> SimulationReport:
        if days < 0:
            raise ValueError("days must be non-negative")
        return self.run_until(self.store.current_tick + days * DAY_MINUTES)

    def report(
        self,
        *,
        processed_turns: int = 0,
        rejected_actions: int = 0,
        provider_errors: int = 0,
    ) -> SimulationReport:
        events = self.store.list_events()
        counts = Counter(event["event_type"] for event in events)
        state = self.store.dump_state()
        completed = [
            goal["goal_id"]
            for goal in state["goals"]
            if goal["status"] == "completed"
        ]
        resolved_arcs = len(
            [arc for arc in state["story_arcs"] if arc["status"] == "resolved"]
        )
        autonomous_types = {
            "goal_created",
            "invitation_created",
            "invitation_kept",
            "invitation_declined",
            "promise_made",
            "promise_fulfilled",
            "promise_broken",
            "story_arc_started",
            "story_arc_resolved",
        }
        autonomous_count = sum(counts[event_type] for event_type in autonomous_types)
        autonomous_ratio = autonomous_count / len(events) if events else 0.0
        return SimulationReport(
            current_tick=self.store.current_tick,
            elapsed_days=self.store.current_tick / DAY_MINUTES,
            processed_turns=processed_turns,
            rejected_actions=rejected_actions,
            provider_errors=provider_errors,
            event_counts=dict(sorted(counts.items())),
            agents=state["agents"],
            completed_goals=completed,
            memory_count=len(self.store.list_memories()),
            resolved_story_arcs=resolved_arcs,
            autonomous_event_ratio=autonomous_ratio,
        )
