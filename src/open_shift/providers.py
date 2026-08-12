from __future__ import annotations

import hashlib
from typing import Protocol

from .models import ActionProposal, ActionType, DecisionContext, GoalStatus


class ModelProvider(Protocol):
    """A provider may propose actions but never mutate world state."""

    def decide(self, context: DecisionContext) -> ActionProposal:
        ...


class MockProvider:
    """Deterministic policy used for tests and headless simulation."""

    def _choice(
        self, context: DecisionContext, modulo: int, *, salt: str = "action"
    ) -> int:
        key = (
            f"{context.seed}:{context.tick}:{context.actor.agent_id}:{salt}"
        ).encode()
        digest = hashlib.blake2b(key, digest_size=8).digest()
        return int.from_bytes(digest, "big") % modulo

    def _target(self, context: DecisionContext) -> str | None:
        others = sorted(
            agent.agent_id
            for agent in context.agents
            if agent.agent_id != context.actor.agent_id
        )
        if not others:
            return None
        arc_targets = sorted(
            {
                arc.target_id
                for arc in context.story_arcs
                if arc.target_id in others and arc.status == "active"
            }
        )
        candidates = arc_targets or others
        last_seen = {candidate: -1 for candidate in candidates}
        for memory in context.memories:
            for candidate in candidates:
                if candidate in memory.tags:
                    last_seen[candidate] = max(last_seen[candidate], memory.tick)
        oldest_tick = min(last_seen.values())
        least_recent = sorted(
            candidate
            for candidate, remembered_tick in last_seen.items()
            if remembered_tick == oldest_tick
        )
        return least_recent[
            self._choice(context, len(least_recent), salt="memory_aware_target")
        ]

    def decide(self, context: DecisionContext) -> ActionProposal:
        actor = context.actor
        active_goals = [
            goal for goal in context.goals if goal.status is GoalStatus.ACTIVE
        ]

        if actor.fatigue >= 0.72:
            return ActionProposal(
                action_type=ActionType.REST,
                duration_minutes=240,
                reason_code="fatigue_threshold",
            )

        if actor.money < 20:
            return ActionProposal(
                action_type=ActionType.WORK,
                duration_minutes=240,
                reason_code="money_floor",
            )

        savings_goal = next(
            (goal for goal in active_goals if goal.kind == "savings"), None
        )
        choice = self._choice(context, 8, salt="action")
        if savings_goal is not None and actor.money < savings_goal.target_value and choice < 2:
            return ActionProposal(
                action_type=ActionType.WORK,
                duration_minutes=240,
                reason_code="savings_goal",
            )

        target = self._target(context)
        if choice == 0:
            return ActionProposal(
                action_type=ActionType.WORK,
                duration_minutes=240,
                reason_code="routine_work",
            )
        if choice == 1:
            if actor.fatigue >= 0.42:
                return ActionProposal(
                    action_type=ActionType.REST,
                    duration_minutes=180,
                    reason_code="preventive_rest",
                )
            location = context.locations[
                self._choice(context, len(context.locations), salt="restless_location")
            ]
            return ActionProposal(
                action_type=ActionType.TRAVEL,
                location=location,
                duration_minutes=30,
                reason_code="change_of_scene",
            )
        if choice == 2 and target is not None:
            return ActionProposal(
                action_type=ActionType.MESSAGE,
                target_id=target,
                reason_code="maintain_relationship",
            )
        if choice == 3 and target is not None:
            return ActionProposal(
                action_type=ActionType.VISIT_BAR,
                target_id=target,
                location="va11_hall_a",
                duration_minutes=120,
                reason_code="social_visit",
            )
        if choice == 4:
            location = context.locations[
                self._choice(context, len(context.locations), salt="travel_location")
            ]
            return ActionProposal(
                action_type=ActionType.TRAVEL,
                location=location,
                duration_minutes=30,
                reason_code="scheduled_travel",
            )
        if choice == 5 and target is not None:
            return ActionProposal(
                action_type=ActionType.INVITE,
                target_id=target,
                location="va11_hall_a",
                duration_minutes=360,
                reason_code="social_invitation",
            )
        if choice == 6 and target is not None:
            return ActionProposal(
                action_type=ActionType.PROMISE,
                target_id=target,
                duration_minutes=360,
                reason_code="support_friend",
            )
        if target is not None:
            target_agent = next(a for a in context.agents if a.agent_id == target)
            if target_agent.location == actor.location:
                return ActionProposal(
                    action_type=ActionType.TALK,
                    target_id=target,
                    reason_code="co_located_conversation",
                )
            return ActionProposal(
                action_type=ActionType.MESSAGE,
                target_id=target,
                reason_code="remote_conversation",
            )

        return ActionProposal(
            action_type=ActionType.REST,
            duration_minutes=120,
            reason_code="no_available_target",
        )
