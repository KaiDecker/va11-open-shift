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
    """Create the three-agent demo exactly once and return its engine."""

    if not store.list_agents():
        agents = [
            AgentState("dana", "Dana", "home", 90, 0.20, "steady", 8 * 60),
            AgentState(
                "dorothy", "Dorothy", "home", 25, 0.35, "playful", 10 * 60
            ),
            AgentState("alma", "Alma", "home", 55, 0.25, "calm", 9 * 60),
        ]
        for agent in agents:
            store.add_agent(agent)
        _seed_relationships(store, [agent.agent_id for agent in agents])
        store.add_goal(Goal("dana_savings", "dana", "savings", None, 150, 0.7))
        store.add_goal(
            Goal("dorothy_savings", "dorothy", "savings", None, 80, 0.8)
        )
        store.add_goal(Goal("alma_savings", "alma", "savings", None, 120, 0.65))
        store.add_goal(
            Goal("dana_trust_dorothy", "dana", "relationship", "dorothy", 0.35, 0.6)
        )
        store.add_goal(
            Goal("dorothy_trust_alma", "dorothy", "relationship", "alma", 0.35, 0.7)
        )
        store.add_goal(
            Goal("alma_trust_dana", "alma", "relationship", "dana", 0.35, 0.55)
        )

    engine = SimulationEngine(store, provider, seed=seed)
    engine.bootstrap_schedule()
    return engine
