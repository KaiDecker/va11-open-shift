"""Bridge adapter that exposes persistent Agent world events as safe scenes."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .bridge import BridgeError, SPEAKER_PORTRAITS, SceneLine, ScenePackage
from .byok import BYOKBudgetExceeded
from .dialogue import (
    DialogueTurnContext,
    DialogueUtterance,
    validate_dialogue_output,
)
from .engine import SimulationEngine
from .models import DAY_MINUTES
from .providers import ModelProvider, MockProvider
from .scenario import create_demo_world
from .store import WorldStore


_AGENT_IDS = ("dana", "dorothy", "alma", "stella", "sei")
_NON_NARRATIVE_EVENTS = {
    "action_rejected",
    "agent_dialogue_completed",
    "dialogue_provider_error",
    "player_scene_ack",
    "provider_error",
}


class WorldSceneService:
    """Turn authoritative world events into bounded GameMaker scene packages.

    World advancement is committed before external dialogue calls begin. The
    pending request record prevents a retry from advancing the world twice,
    while the final serialized scene makes replay free of additional API calls.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        provider_factory: Callable[[], ModelProvider] | None = None,
        error_reporter: Callable[[str, Exception], None] | None = None,
        seed: int = 7,
        advance_minutes: int = DAY_MINUTES,
    ) -> None:
        if advance_minutes < 0 or advance_minutes > 30 * DAY_MINUTES:
            raise ValueError("advance_minutes must be between 0 and 43200")
        self.db_path = Path(db_path)
        self.provider_factory = provider_factory or MockProvider
        self.error_reporter = error_reporter
        self.seed = seed
        self.advance_minutes = advance_minutes
        self._lock = threading.RLock()

    def _report_error(self, operation: str, error: Exception) -> None:
        if self.error_reporter is None:
            return
        try:
            self.error_reporter(operation, error)
        except Exception:
            pass

    def _engine(
        self, store: WorldStore, provider: ModelProvider
    ) -> SimulationEngine:
        return create_demo_world(store, provider, seed=self.seed)

    @staticmethod
    def _participants(event: Mapping[str, Any]) -> tuple[str, str]:
        event_id = int(event["event_id"])
        raw_actor = event.get("actor_id")
        actor = raw_actor if raw_actor in _AGENT_IDS else "dana"
        raw_target = event.get("target_id")
        if raw_target in _AGENT_IDS and raw_target != actor:
            return actor, str(raw_target)
        candidates = [item for item in _AGENT_IDS if item != actor]
        return actor, candidates[event_id % len(candidates)]

    @staticmethod
    def _event_premise(
        event: Mapping[str, Any], display_names: Mapping[str, str], current_tick: int
    ) -> str:
        actor_id, target_id = WorldSceneService._participants(event)
        actor = display_names.get(actor_id, actor_id.title())
        target = display_names.get(target_id, target_id.title())
        event_type = str(event.get("event_type", "world_event"))
        templates = {
            "worked": f"{actor}结束工作后在酒吧遇到了{target}。",
            "rested": f"{actor}休息后在酒吧遇到{target}，眼前正好有件具体小事可聊。",
            "travelled": f"{actor}从城里别处来到酒吧，在吧台碰见{target}。",
            "message_sent": f"{actor}和{target}继续聊起之前发过的那条消息。",
            "talked": f"{actor}与{target}在吧台继续先前没有说完的话题。",
            "bar_visited": f"{actor}来到仍在营业的 VA-11 Hall-A，正好遇见{target}。",
            "invitation_created": f"{actor}想当面确认对{target}发出的邀请。",
            "invitation_kept": f"{actor}与{target}兑现了之前的邀约。",
            "invitation_declined": f"{actor}与{target}谈起没有成行的邀约。",
            "promise_made": f"{actor}想和{target}确认刚作出的承诺。",
            "promise_fulfilled": f"{actor}与{target}谈起已经兑现的承诺。",
            "promise_broken": f"{actor}与{target}必须面对一个没有兑现的承诺。",
            "story_arc_resolved": f"{actor}与{target}回顾一件终于有结果的事。",
            "goal_completed": f"{actor}完成了一件具体打算，并把结果告诉{target}。",
            "goal_created": f"{actor}有了新的具体打算，想听听{target}的看法。",
        }
        return templates.get(
            event_type,
            f"{actor}与{target}在酒吧碰见，谈起眼前发生的一件小事。",
        )

    @staticmethod
    def _fallback_scene(
        event: Mapping[str, Any], display_names: Mapping[str, str], current_tick: int
    ) -> ScenePackage:
        event_id = int(event["event_id"])
        speaker, target_speaker = WorldSceneService._participants(event)
        actor = display_names.get(speaker, speaker.title())
        target = display_names.get(target_speaker, target_speaker.title())
        lines = (
            SceneLine(
                "world_1",
                speaker,
                SPEAKER_PORTRAITS[speaker],
                "worry",
                f"{target}，我有件事想和你谈谈。",
            ),
            SceneLine(
                "world_2",
                target_speaker,
                SPEAKER_PORTRAITS[target_speaker],
                "neutral",
                f"我在听，{actor}。慢慢说。",
            ),
            SceneLine(
                "world_3",
                speaker,
                SPEAKER_PORTRAITS[speaker],
                "happy",
                "我们一起想想接下来该怎么办。",
            ),
        )
        return ScenePackage(f"world_event_{event_id}", lines)

    def _generated_scene(
        self,
        event: Mapping[str, Any],
        display_names: Mapping[str, str],
        current_tick: int,
        engine: SimulationEngine,
        provider: ModelProvider,
    ) -> ScenePackage:
        generator = getattr(provider, "generate_dialogue_line", None)
        if not callable(generator):
            return self._fallback_scene(event, display_names, current_tick)

        event_id = int(event["event_id"])
        scene_id = f"world_event_{event_id}"
        participants = self._participants(event)
        # Three dependent Agent turns keep the exchange responsive while each
        # speaker still sees the public result of the previous turn.
        turn_count = 3
        speaker_order = tuple(
            participants[index % len(participants)] for index in range(turn_count)
        )
        premise = self._event_premise(event, display_names, current_tick)
        transcript: list[DialogueUtterance] = []
        lines: list[SceneLine] = []
        for index, speaker_id in enumerate(speaker_order):
            context = DialogueTurnContext(
                scene_id=scene_id,
                turn_index=index,
                turn_count=turn_count,
                premise=premise,
                speaker=engine.context_for_agent(current_tick, speaker_id),
                participant_ids=participants,
                transcript=tuple(transcript),
            )
            proposed = generator(context)
            draft = validate_dialogue_output(
                {
                    "expression_id": proposed.expression_id,
                    "text": proposed.text,
                },
                context,
            )
            transcript.append(DialogueUtterance(speaker_id, draft.text))
            lines.append(
                SceneLine(
                    f"dialogue_{index + 1}",
                    speaker_id,
                    SPEAKER_PORTRAITS[speaker_id],
                    draft.expression_id,
                    draft.text,
                )
            )
        return ScenePackage(scene_id, tuple(lines))

    @staticmethod
    def _find_event(store: WorldStore, event_id: int) -> dict[str, Any]:
        event = next(
            (item for item in store.list_events() if item["event_id"] == event_id),
            None,
        )
        if event is None:
            raise ValueError("persisted bridge event was missing")
        return event

    @staticmethod
    def _dialogue_memory_summary(
        scene: ScenePackage, display_names: Mapping[str, str]
    ) -> str:
        transcript = " / ".join(
            f"{display_names.get(line.speaker_id, line.speaker_id)}：{line.text}"
            for line in scene.lines
        )
        return f"在 VA-11 Hall-A 与熟人完成了一次交谈。公开对话：{transcript}"[:480]

    @staticmethod
    def _remember_generated_dialogue(
        store: WorldStore,
        scene: ScenePackage,
        source_event_id: int,
    ) -> None:
        if not scene.lines or not all(
            line.line_id.startswith("dialogue_") for line in scene.lines
        ):
            return
        participants = tuple(dict.fromkeys(line.speaker_id for line in scene.lines))
        if len(participants) < 2:
            return
        display_names = {
            agent.agent_id: agent.display_name for agent in store.list_agents()
        }
        summary = WorldSceneService._dialogue_memory_summary(scene, display_names)
        memory_event_id = store.append_event(
            store.current_tick,
            "agent_dialogue_completed",
            participants[0],
            participants[1],
            {
                "scene_id": scene.scene_id,
                "source_event_id": source_event_id,
                "participants": list(participants),
                "summary": summary,
            },
        )
        tags = {"dialogue", "va11_hall_a", *participants}
        for participant_id in participants:
            store.append_memory(
                participant_id,
                memory_event_id,
                0.75,
                summary,
                tags,
            )

    def open_scene(self, request: Mapping[str, Any]) -> ScenePackage:
        with self._lock, WorldStore(self.db_path) as store:
            request_id = str(request["request_id"])
            meta_key = f"bridge_open:{request_id}"
            request_json = json.dumps(
                dict(request), separators=(",", ":"), sort_keys=True
            )
            provider = self.provider_factory()
            engine = self._engine(store, provider)
            legacy_record = False

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
                    persisted_scene = record.get("scene")
                    if isinstance(persisted_scene, dict):
                        return ScenePackage.from_dict(persisted_scene)
                    event = self._find_event(store, int(record["event_id"]))
                    issue_tick = int(record["issue_tick"])
                    legacy_record = record.get("dialogue_version") is None
                else:
                    target_tick = store.current_tick + self.advance_minutes
                    if target_tick > store.current_tick:
                        engine.run_until(target_tick)
                    events = [
                        item
                        for item in store.list_events()
                        if item["event_type"] not in _NON_NARRATIVE_EVENTS
                    ]
                    if not events:
                        store.append_event(
                            store.current_tick,
                            "world_snapshot",
                            None,
                            payload={"source": "stage_5_bridge"},
                        )
                        events = [store.list_events()[-1]]
                    event = events[-1]
                    issue_tick = store.current_tick
                    record = {
                        "dialogue_version": 1,
                        "event_id": event["event_id"],
                        "issue_tick": issue_tick,
                        "request": request_json,
                        "scene": None,
                    }
                    store.set_meta(
                        meta_key,
                        json.dumps(record, separators=(",", ":"), sort_keys=True),
                    )

            names = {
                agent.agent_id: agent.display_name for agent in store.list_agents()
            }
            if legacy_record:
                scene = self._fallback_scene(event, names, issue_tick)
            else:
                try:
                    scene = self._generated_scene(
                        event, names, issue_tick, engine, provider
                    )
                except BYOKBudgetExceeded as exc:
                    self._report_error("dialogue generation", exc)
                    store.append_event(
                        issue_tick,
                        "dialogue_provider_error",
                        event.get("actor_id"),
                        event.get("target_id"),
                        {
                            "error_type": type(exc).__name__,
                            "source_event_id": event["event_id"],
                        },
                    )
                    raise BridgeError(
                        429,
                        "provider_budget_exhausted",
                        "the configured provider call budget was exhausted",
                    ) from None
                except Exception as exc:
                    self._report_error("dialogue generation", exc)
                    store.append_event(
                        issue_tick,
                        "dialogue_provider_error",
                        event.get("actor_id"),
                        event.get("target_id"),
                        {
                            "error_type": type(exc).__name__,
                            "source_event_id": event["event_id"],
                        },
                    )
                    scene = self._fallback_scene(event, names, issue_tick)

            with store.transaction():
                latest = json.loads(store.get_meta(meta_key) or "{}")
                if latest.get("request") != request_json:
                    raise BridgeError(
                        409,
                        "request_id_conflict",
                        "request_id was already used with different content",
                    )
                persisted_scene = latest.get("scene")
                if isinstance(persisted_scene, dict):
                    return ScenePackage.from_dict(persisted_scene)
                latest["scene"] = scene.to_dict()
                store.set_meta(
                    meta_key,
                    json.dumps(
                        latest,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
                store.set_meta(f"bridge_scene:{scene.scene_id}", event["event_id"])
                store.set_meta(
                    f"bridge_scene_payload:{scene.scene_id}",
                    json.dumps(
                        scene.to_dict(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            return scene

    def ack_scene(self, request: Mapping[str, Any]) -> None:
        scene_id = str(request["scene_id"])
        with self._lock, WorldStore(self.db_path) as store:
            issued = store.get_meta(f"bridge_scene:{scene_id}")
            try:
                event_id = int(issued or "")
            except ValueError:
                raise KeyError("scene_id was not issued by the bridge") from None
            exists = any(
                event["event_id"] == event_id for event in store.list_events()
            )
            if not exists:
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
                scene_payload = store.get_meta(f"bridge_scene_payload:{scene_id}")
                if scene_payload is not None:
                    scene = ScenePackage.from_dict(json.loads(scene_payload))
                    self._remember_generated_dialogue(store, scene, event_id)
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
