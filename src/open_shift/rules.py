from __future__ import annotations

from dataclasses import replace

from .models import (
    ActionProposal,
    ActionResult,
    ActionType,
    AgentState,
    Goal,
    GoalStatus,
    Relationship,
)
from .store import WorldStore


WORK_WAGE = 24
BAR_VISIT_COST = 12
DEFAULT_SOCIAL_DELAY = 6 * 60


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
        # Economic outcomes belong to the world rules, never to model output.
        wage = WORK_WAGE
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

    def _advance_social_arcs(
        self, tick: int, actor: AgentState, target: AgentState
    ) -> None:
        for owner, other in ((actor, target), (target, actor)):
            for arc in self.store.advance_story_arcs(owner.agent_id, other.agent_id, tick):
                event_id = self.store.append_event(
                    tick,
                    "story_arc_resolved",
                    owner.agent_id,
                    other.agent_id,
                    {"arc_id": arc.arc_id, "kind": arc.kind},
                )
                self.store.append_memory(
                    owner.agent_id,
                    event_id,
                    0.85,
                    f"Resolved {arc.kind} with {other.display_name}.",
                    ["story_arc", "milestone", other.agent_id],
                )
                next_arc_id = self.store.add_story_arc(
                    owner.agent_id,
                    other.agent_id,
                    "keep_in_touch",
                    tick,
                    required_progress=4,
                )
                self.store.append_event(
                    tick,
                    "story_arc_started",
                    owner.agent_id,
                    other.agent_id,
                    {"arc_id": next_arc_id, "kind": "keep_in_touch"},
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
        self._advance_social_arcs(tick, actor, target)
        return ActionResult(True, "accepted", event_id)

    def _execute_visit_bar(
        self, tick: int, actor: AgentState, action: ActionProposal
    ) -> ActionResult:
        # A provider may choose to visit, but it cannot choose the price.
        cost = BAR_VISIT_COST
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
            self._advance_social_arcs(tick, actor, target)
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
        self._advance_social_arcs(tick, actor, target)
        return ActionResult(True, "accepted", event_id)

    def _execute_invite(
        self, tick: int, actor: AgentState, action: ActionProposal
    ) -> ActionResult:
        target = self._validate_target(actor, action.target_id)
        if target is None:
            return ActionResult(False, "invalid_target")
        if action.location not in self.locations:
            return ActionResult(False, "unknown_location")
        delay = action.duration_minutes or DEFAULT_SOCIAL_DELAY
        proposed_tick = tick + max(60, min(delay, 720))
        event_id = self._event(
            tick,
            "invitation_created",
            actor.agent_id,
            action,
            {"location": action.location, "proposed_tick": proposed_tick},
        )
        invitation_id = self.store.add_invitation(
            actor.agent_id,
            target.agent_id,
            action.location,
            proposed_tick,
            event_id,
        )
        self.store.schedule_event(
            proposed_tick,
            "invitation_due",
            target.agent_id,
            {"invitation_id": invitation_id},
        )
        self._remember_social_event(actor, target, event_id, "Invited", 0.45)
        self._advance_social_arcs(tick, actor, target)
        return ActionResult(True, "accepted", event_id)

    def _execute_promise(
        self, tick: int, actor: AgentState, action: ActionProposal
    ) -> ActionResult:
        target = self._validate_target(actor, action.target_id)
        if target is None:
            return ActionResult(False, "invalid_target")
        delay = action.duration_minutes or DEFAULT_SOCIAL_DELAY
        due_tick = tick + max(60, min(delay, 720))
        event_id = self._event(
            tick,
            "promise_made",
            actor.agent_id,
            action,
            {"due_tick": due_tick},
        )
        commitment_id = self.store.add_commitment(
            actor.agent_id, target.agent_id, due_tick, event_id
        )
        self.store.schedule_event(
            due_tick,
            "commitment_due",
            actor.agent_id,
            {"commitment_id": commitment_id},
        )
        self._remember_social_event(actor, target, event_id, "Promised to help", 0.7)
        self._advance_social_arcs(tick, actor, target)
        return ActionResult(True, "accepted", event_id)

    def resolve_invitation(self, tick: int, invitation_id: int) -> None:
        with self.store.transaction():
            self._resolve_invitation(tick, invitation_id)

    def _resolve_invitation(self, tick: int, invitation_id: int) -> None:
        invitation = self.store.get_invitation(invitation_id)
        if invitation is None or invitation.status != "pending":
            return
        inviter = self.store.get_agent(invitation.inviter_id)
        invitee = self.store.get_agent(invitation.invitee_id)
        if inviter is None or invitee is None:
            self.store.set_invitation_status(invitation_id, "declined")
            return
        relationship = self.store.get_relationship(invitee.agent_id, inviter.agent_id)
        accepted = relationship.trust >= -0.25 and invitee.fatigue < 0.9
        status = "accepted" if accepted else "declined"
        self.store.set_invitation_status(invitation_id, status)
        if accepted:
            self.store.update_agent(replace(inviter, location=invitation.location))
            self.store.update_agent(replace(invitee, location=invitation.location))
        event_type = "invitation_kept" if accepted else "invitation_declined"
        event_id = self.store.append_event(
            tick,
            event_type,
            invitee.agent_id,
            inviter.agent_id,
            {"invitation_id": invitation_id, "location": invitation.location},
        )
        self._remember_social_event(invitee, inviter, event_id, "Met with" if accepted else "Declined", 0.6)
        delta = 0.05 if accepted else -0.03
        self._change_relationship(inviter.agent_id, invitee.agent_id, delta, delta)
        self._change_relationship(invitee.agent_id, inviter.agent_id, delta, delta)
        if accepted:
            self._advance_social_arcs(tick, inviter, invitee)

    def resolve_commitment(self, tick: int, commitment_id: int) -> None:
        with self.store.transaction():
            self._resolve_commitment(tick, commitment_id)

    def _resolve_commitment(self, tick: int, commitment_id: int) -> None:
        commitment = next(
            (
                item
                for item in self.store.list_commitments(status="pending")
                if item.commitment_id == commitment_id
            ),
            None,
        )
        if commitment is None:
            return
        actor = self.store.get_agent(commitment.actor_id)
        target = self.store.get_agent(commitment.target_id)
        if actor is None or target is None:
            return
        relationship = self.store.get_relationship(actor.agent_id, target.agent_id)
        fulfilled = relationship.trust >= -0.5 and actor.fatigue < 0.95
        status = "fulfilled" if fulfilled else "broken"
        event_id = self.store.append_event(
            tick,
            "promise_fulfilled" if fulfilled else "promise_broken",
            actor.agent_id,
            target.agent_id,
            {"commitment_id": commitment_id},
        )
        self.store.resolve_commitment(commitment_id, status, event_id)
        delta = 0.08 if fulfilled else -0.12
        self._change_relationship(actor.agent_id, target.agent_id, delta, delta)
        self._change_relationship(target.agent_id, actor.agent_id, delta, delta)
        self._remember_social_event(
            actor,
            target,
            event_id,
            "Kept a promise to" if fulfilled else "Broke a promise to",
            0.9,
        )
        if fulfilled:
            self._advance_social_arcs(tick, actor, target)

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
                self._create_followup_goal(tick, actor, goal, event_id)

    def _create_followup_goal(
        self, tick: int, actor: AgentState, completed: Goal, source_event_id: int
    ) -> None:
        if completed.kind == "savings":
            target_value = completed.target_value + 120
        elif completed.kind == "relationship" and completed.target_id is not None:
            target_value = min(0.9, completed.target_value + 0.2)
            if target_value <= completed.target_value:
                return
        else:
            return
        goal_id = f"{actor.agent_id}_{completed.kind}_{source_event_id}"
        self.store.add_goal(
            Goal(
                goal_id,
                actor.agent_id,
                completed.kind,
                completed.target_id,
                target_value,
                completed.priority,
                metadata={"previous_goal_id": completed.goal_id},
            )
        )
        new_event_id = self.store.append_event(
            tick,
            "goal_created",
            actor.agent_id,
            completed.target_id,
            {
                "goal_id": goal_id,
                "kind": completed.kind,
                "target_value": target_value,
            },
        )
        self.store.append_memory(
            actor.agent_id,
            new_event_id,
            0.65,
            f"Set a new {completed.kind} goal.",
            ["goal", "plan"],
        )
