from __future__ import annotations

from dataclasses import replace

from .models import (
    ActionProposal,
    ActionResult,
    ActionType,
    AgentState,
    GoalStatus,
    Relationship,
)
from .store import WorldStore


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


class RuleEngine:
    """Validates provider proposals and commits legal world transitions."""

    def __init__(self, store: WorldStore, locations: tuple[str, ...]) -> None:
        self.store = store
        self.locations = locations

    def execute(self, tick: int, actor_id: str, action: ActionProposal) -> ActionResult:
        actor = self.store.get_agent(actor_id)
        if actor is None:
            return ActionResult(False, "unknown_actor")

        validator = getattr(self, f"_execute_{action.action_type.value}", None)
        if validator is None:
            return ActionResult(False, "unsupported_action")

        with self.store.transaction():
            result = validator(tick, actor, action)
            if result.accepted:
                self._evaluate_goals(tick, actor_id)
            return result

    def _event(
        self,
        tick: int,
        event_type: str,
        actor_id: str,
        action: ActionProposal,
        payload: dict[str, object],
    ) -> int:
        return self.store.append_event(
            tick=tick,
            event_type=event_type,
            actor_id=actor_id,
            target_id=action.target_id,
            payload={"reason_code": action.reason_code, **payload},
        )

    def _execute_travel(
        self, tick: int, actor: AgentState, action: ActionProposal
    ) -> ActionResult:
        if action.location not in self.locations:
            return ActionResult(False, "unknown_location")
        updated = replace(
            actor,
            location=action.location,
            fatigue=_clamp(actor.fatigue + 0.04, 0.0, 1.0),
            mood="curious",
        )
        self.store.update_agent(updated)
        event_id = self._event(
            tick, "travelled", actor.agent_id, action, {"location": action.location}
        )
        self.store.append_memory(
            actor.agent_id,
            event_id,
            0.15,
            f"Traveled to {action.location}.",
            ["travel", action.location or "unknown"],
        )
        return ActionResult(True, "accepted", event_id)

    def _execute_work(
        self, tick: int, actor: AgentState, action: ActionProposal
    ) -> ActionResult:
        wage = int(action.metadata.get("wage", 24))
        if wage < 0:
            return ActionResult(False, "negative_wage")
        updated = replace(
            actor,
            location="work",
            money=actor.money + wage,
            fatigue=_clamp(actor.fatigue + 0.22, 0.0, 1.0),
            mood="focused",
        )
        self.store.update_agent(updated)
        event_id = self._event(
            tick, "worked", actor.agent_id, action, {"wage": wage}
        )
        self.store.append_memory(
            actor.agent_id,
            event_id,
            0.2,
            f"Worked and earned {wage} credits.",
            ["work", "money"],
        )
        return ActionResult(True, "accepted", event_id)

    def _execute_rest(
        self, tick: int, actor: AgentState, action: ActionProposal
    ) -> ActionResult:
        updated = replace(
            actor,
            location="home",
            fatigue=_clamp(actor.fatigue - 0.45, 0.0, 1.0),
            mood="rested",
        )
        self.store.update_agent(updated)
        event_id = self._event(
            tick,
            "rested",
            actor.agent_id,
            action,
            {"duration_minutes": max(0, action.duration_minutes)},
        )
        self.store.append_memory(
            actor.agent_id,
            event_id,
            0.08,
            "Rested at home.",
            ["rest"],
        )
        return ActionResult(True, "accepted", event_id)

    def _validate_target(
        self, actor: AgentState, target_id: str | None
    ) -> AgentState | None:
        if target_id is None or target_id == actor.agent_id:
            return None
        return self.store.get_agent(target_id)

    def _change_relationship(
        self,
        source_id: str,
        target_id: str,
        trust_delta: float,
        warmth_delta: float,
    ) -> Relationship:
        current = self.store.get_relationship(source_id, target_id)
        updated = replace(
            current,
            trust=_clamp(current.trust + trust_delta, -1.0, 1.0),
            warmth=_clamp(current.warmth + warmth_delta, -1.0, 1.0),
        )
        self.store.upsert_relationship(updated)
        return updated

    def _remember_social_event(
        self,
        actor: AgentState,
        target: AgentState,
        event_id: int,
        verb: str,
        importance: float,
    ) -> None:
        self.store.append_memory(
            actor.agent_id,
            event_id,
            importance,
            f"{verb} {target.display_name}.",
            ["social", target.agent_id],
        )
        self.store.append_memory(
            target.agent_id,
            event_id,
            importance,
            f"{actor.display_name} {verb.lower()} me.",
            ["social", actor.agent_id],
        )

    def _execute_message(
        self, tick: int, actor: AgentState, action: ActionProposal
    ) -> ActionResult:
        target = self._validate_target(actor, action.target_id)
        if target is None:
            return ActionResult(False, "invalid_target")
        relationship = self._change_relationship(
            actor.agent_id, target.agent_id, 0.02, 0.03
        )
        event_id = self._event(
            tick,
            "message_sent",
            actor.agent_id,
            action,
            {"trust": relationship.trust, "warmth": relationship.warmth},
        )
        self._remember_social_event(actor, target, event_id, "Messaged", 0.28)
        return ActionResult(True, "accepted", event_id)

    def _execute_visit_bar(
        self, tick: int, actor: AgentState, action: ActionProposal
    ) -> ActionResult:
        cost = action.amount if action.amount > 0 else 12
        if actor.money < cost:
            return ActionResult(False, "insufficient_funds")
        target = self._validate_target(actor, action.target_id)
        if action.target_id is not None and target is None:
            return ActionResult(False, "invalid_target")
        updated = replace(
            actor,
            location="va11_hall_a",
            money=actor.money - cost,
            fatigue=_clamp(actor.fatigue + 0.08, 0.0, 1.0),
            mood="social",
        )
        self.store.update_agent(updated)
        payload: dict[str, object] = {"cost": cost}
        if target is not None:
            relationship = self._change_relationship(
                actor.agent_id, target.agent_id, 0.01, 0.04
            )
            payload.update(
                {"trust": relationship.trust, "warmth": relationship.warmth}
            )
        event_id = self._event(
            tick, "visited_bar", actor.agent_id, action, payload
        )
        self.store.append_memory(
            actor.agent_id,
            event_id,
            0.4,
            "Visited VA-11 Hall-A.",
            ["bar", "social"],
        )
        if target is not None:
            self.store.append_memory(
                target.agent_id,
                event_id,
                0.3,
                f"{actor.display_name} visited the bar with me in mind.",
                ["bar", "social", actor.agent_id],
            )
        return ActionResult(True, "accepted", event_id)

    def _execute_talk(
        self, tick: int, actor: AgentState, action: ActionProposal
    ) -> ActionResult:
        target = self._validate_target(actor, action.target_id)
        if target is None:
            return ActionResult(False, "invalid_target")
        if target.location != actor.location:
            return ActionResult(False, "not_co_located")
        relationship = self._change_relationship(
            actor.agent_id, target.agent_id, 0.02, 0.02
        )
        event_id = self._event(
            tick,
            "conversation",
            actor.agent_id,
            action,
            {
                "location": actor.location,
                "trust": relationship.trust,
                "warmth": relationship.warmth,
            },
        )
        self._remember_social_event(actor, target, event_id, "Talked with", 0.32)
        return ActionResult(True, "accepted", event_id)

    def _evaluate_goals(self, tick: int, actor_id: str) -> None:
        actor = self.store.get_agent(actor_id)
        if actor is None:
            return
        for goal in self.store.list_goals(actor_id, GoalStatus.ACTIVE):
            completed = False
            observed_value = 0.0
            if goal.kind == "savings":
                observed_value = float(actor.money)
                completed = observed_value >= goal.target_value
            elif goal.kind == "relationship" and goal.target_id is not None:
                relationship = self.store.get_relationship(
                    actor.agent_id, goal.target_id
                )
                observed_value = relationship.trust
                completed = observed_value >= goal.target_value
            if completed:
                self.store.set_goal_status(goal.goal_id, GoalStatus.COMPLETED)
                event_id = self.store.append_event(
                    tick,
                    "goal_completed",
                    actor.agent_id,
                    goal.target_id,
                    {
                        "goal_id": goal.goal_id,
                        "kind": goal.kind,
                        "observed_value": observed_value,
                        "target_value": goal.target_value,
                    },
                )
                self.store.append_memory(
                    actor.agent_id,
                    event_id,
                    0.9,
                    f"Completed goal {goal.goal_id}.",
                    ["goal", "milestone"],
                )
