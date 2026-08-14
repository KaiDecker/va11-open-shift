from __future__ import annotations

from .engine import SimulationEngine
from .models import AgentState, Goal, Relationship
from .providers import ModelProvider
from .store import WorldStore


def _seed_relationships(store: WorldStore, agent_ids: list[str]) -> None:
    for source in agent_ids:
        for target in agent_ids:
            if source == target:
                continue
            store.upsert_relationship(
                Relationship(
                    source_id=source,
                    target_id=target,
                    trust=0.12,
                    warmth=0.18,
                )
            )


def create_demo_world(
    store: WorldStore,
    provider: ModelProvider,
    *,
    seed: int = 7,
) -> SimulationEngine:
    """Create the post-good-ending five-Agent world once and return its engine."""

    timeline_id = store.get_meta("timeline_id")
    if timeline_id != "after_main_story":
        store.set_meta("timeline_id", "after_main_story")

    if not store.list_agents():
        agents = [
            AgentState("dana", "Dana", "home", 90, 0.20, "steady", 8 * 60),
            AgentState(
                "dorothy", "Dorothy", "home", 25, 0.35, "playful", 10 * 60
            ),
            AgentState("alma", "Alma", "home", 55, 0.25, "calm", 9 * 60),
            AgentState("stella", "Stella", "home", 110, 0.18, "bright", 11 * 60),
            AgentState("sei", "Sei", "home", 70, 0.30, "earnest", 7 * 60),
        ]
        for agent in agents:
            store.add_agent(agent)
        _seed_relationships(store, [agent.agent_id for agent in agents])
        store.add_goal(Goal("dana_savings", "dana", "savings", None, 150, 0.7))
        store.add_goal(
            Goal("dorothy_savings", "dorothy", "savings", None, 80, 0.8)
        )
        store.add_goal(Goal("alma_savings", "alma", "savings", None, 120, 0.65))
        store.add_goal(Goal("stella_savings", "stella", "savings", None, 180, 0.6))
        store.add_goal(Goal("sei_savings", "sei", "savings", None, 140, 0.75))
        store.add_goal(
            Goal("dana_trust_dorothy", "dana", "relationship", "dorothy", 0.35, 0.6)
        )
        store.add_goal(
            Goal("dorothy_trust_alma", "dorothy", "relationship", "alma", 0.35, 0.7)
        )
        store.add_goal(
            Goal("alma_trust_dana", "alma", "relationship", "dana", 0.35, 0.55)
        )
        social_pairs = [
            ("dana", "dorothy"),
            ("dorothy", "alma"),
            ("alma", "dana"),
            ("stella", "sei"),
            ("sei", "stella"),
        ]
        for owner_id, target_id in social_pairs:
            arc_id = store.add_story_arc(
                owner_id,
                target_id,
                "strengthen_friendship",
                store.current_tick,
                required_progress=3,
            )
            store.append_event(
                store.current_tick,
                "story_arc_started",
                owner_id,
                target_id,
                {"arc_id": arc_id, "kind": "strengthen_friendship"},
            )

    engine = SimulationEngine(store, provider, seed=seed)
    engine.bootstrap_schedule()
    return engine
