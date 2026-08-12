"""Bridge adapter that exposes persistent Agent world events as safe scenes."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .bridge import BridgeError, SceneLine, ScenePackage
from .engine import SimulationEngine
from .models import DAY_MINUTES
from .providers import ModelProvider, MockProvider
from .scenario import create_demo_world
from .store import WorldStore


class WorldSceneService:
    """Turn authoritative world events into bounded GameMaker scene packages.

    Every operation opens its own SQLite connection. This keeps the HTTP server
    threads isolated from SQLite's thread affinity and makes the store the only
    authoritative write boundary.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        provider_factory: Callable[[], ModelProvider] | None = None,
        seed: int = 7,
        advance_minutes: int = DAY_MINUTES,
    ) -> None:
        if advance_minutes < 0 or advance_minutes > 30 * DAY_MINUTES:
            raise ValueError("advance_minutes must be between 0 and 43200")
        self.db_path = Path(db_path)
        self.provider_factory = provider_factory or MockProvider
        self.seed = seed
        self.advance_minutes = advance_minutes
        self._lock = threading.RLock()
        self._issued_scene_ids: set[str] = set()

    def _engine(self, store: WorldStore) -> SimulationEngine:
        return create_demo_world(
            store, self.provider_factory(), seed=self.seed
        )

    @staticmethod
    def _event_text(
        event: Mapping[str, Any], display_names: Mapping[str, str], current_tick: int
    ) -> ScenePackage:
        event_id = int(event["event_id"])
        actor_id = event.get("actor_id") or "dana"
        target_id = event.get("target_id")
        scene_id = f"world_event_{event_id}"
        agent_ids = {"dana", "dorothy", "alma", "stella", "sei"}
        speaker = actor_id if actor_id in agent_ids else "dana"
        target_speaker = (
            target_id
            if target_id in agent_ids and target_id != speaker
            else ("alma" if speaker != "alma" else "dana")
        )
        actor = display_names.get(speaker, speaker.title())
        target = (
            display_names.get(target_speaker, target_speaker.title())
            if target_id in agent_ids
            else ""
        )
        portraits = {
            "dana": "sprite_dana",
            "dorothy": "sprite_doro",
            "alma": "sprite_alma",
            "stella": "sprite_stella",
            "sei": "sprite_sei",
        }
        first = f"{target + '，' if target else ''}我有件事想和你谈谈。"
        second = (
            f"我在听，{actor}。慢慢说。"
            if target
            else "嗯，你说吧。我在听。"
        )
        third = (
            "我们一起想想接下来该怎么办。"
            if target
            else f"今天是第{current_tick // DAY_MINUTES + 1}天，我们会处理好的。"
        )
        lines = (
            SceneLine("world_1", speaker, portraits[speaker], "worry", first),
            SceneLine("world_2", target_speaker, portraits[target_speaker], "neutral", second),
            SceneLine("world_3", speaker, portraits[speaker], "happy", third),
        )
        return ScenePackage(scene_id, lines)

    def open_scene(self, request: Mapping[str, Any]) -> ScenePackage:
        with self._lock, WorldStore(self.db_path) as store:
            request_id = str(request["request_id"])
            meta_key = f"bridge_open:{request_id}"
            request_json = json.dumps(
                dict(request), separators=(",", ":"), sort_keys=True
            )
            with store.transaction():
                prior = store.get_meta(meta_key)
                if prior is not None:
                    record = json.loads(prior)
                    if record.get("request") != request_json:
                        raise BridgeError(
                            409,
                            "request_id_conflict",
                            "request_id was already used with different content",
                        )
                    event = next(
                        (
                            item
                            for item in store.list_events()
                            if item["event_id"] == record["event_id"]
                        ),
                        None,
                    )
                    if event is None:
                        raise ValueError("persisted bridge event was missing")
                    issue_tick = int(record["issue_tick"])
                else:
                    engine = self._engine(store)
                    target_tick = store.current_tick + self.advance_minutes
                    if target_tick > store.current_tick:
                        engine.run_until(target_tick)
                    events = [
                        item
                        for item in store.list_events()
                        if item["event_type"] != "player_scene_ack"
                    ]
                    if not events:
                        store.append_event(
                            store.current_tick,
                            "world_snapshot",
                            None,
                            payload={"source": "stage_4_bridge"},
                        )
                        events = [store.list_events()[-1]]
                    event = events[-1]
                    issue_tick = store.current_tick
                    store.set_meta(
                        meta_key,
                        json.dumps(
                            {
                                "event_id": event["event_id"],
                                "issue_tick": issue_tick,
                                "request": request_json,
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    )
                names = {
                    agent.agent_id: agent.display_name for agent in store.list_agents()
                }
                scene = self._event_text(event, names, issue_tick)
                store.set_meta(f"bridge_scene:{scene.scene_id}", event["event_id"])
            self._issued_scene_ids.add(scene.scene_id)
            if len(self._issued_scene_ids) > 1024:
                self._issued_scene_ids = set(sorted(self._issued_scene_ids)[-512:])
            return scene

    def ack_scene(self, request: Mapping[str, Any]) -> None:
        scene_id = request["scene_id"]
        with self._lock:
            with WorldStore(self.db_path) as store:
                try:
                    event_id = int(str(scene_id).removeprefix("world_event_"))
                except ValueError:
                    raise KeyError("scene_id was not a persistent world event") from None
                exists = any(
                    event["event_id"] == event_id for event in store.list_events()
                )
                issued = store.get_meta(f"bridge_scene:{scene_id}")
                if not exists or issued != str(event_id):
                    raise KeyError("scene_id was not issued by the bridge")
                ack_key = f"bridge_ack:{scene_id}"
                request_key = f"bridge_ack_request:{request['request_id']}"
                request_json = json.dumps(
                    dict(request), separators=(",", ":"), sort_keys=True
                )
                with store.transaction():
                    prior_request = store.get_meta(request_key)
                    if prior_request is not None and prior_request != request_json:
                        raise BridgeError(
                            409,
                            "request_id_conflict",
                            "request_id was already used with different content",
                        )
                    if store.get_meta(ack_key) is not None:
                        store.set_meta(request_key, request_json)
                        return
                    ack_event_id = store.append_event(
                        store.current_tick,
                        "player_scene_ack",
                        None,
                        payload={
                            "scene_id": scene_id,
                            "client_session_id": request["client_session_id"],
                            "outcome": request["outcome"],
                        },
                    )
                    store.set_meta(ack_key, ack_event_id)
                    store.set_meta(request_key, request_json)
