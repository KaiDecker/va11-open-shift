"""Bridge adapter that exposes persistent Agent world events as safe scenes."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .bridge import (
    BridgeError,
    OrderResolution,
    SPEAKER_PORTRAITS,
    SceneLine,
    ScenePackage,
)
from .byok import BYOKBudgetExceeded
from .dialogue import (
    DialogueTurnContext,
    DialogueUtterance,
    PlayerDialogueTurnContext,
    validate_dialogue_output,
    validate_player_dialogue_output,
)
from .drinks import (
    AlcoholRequirement,
    DrinkOrder,
    DrinkSubmission,
    ServiceCategory,
    ServiceResult,
    evaluate_service,
    order_for_customer,
    service_income,
)
from .engine import SimulationEngine
from .models import DAY_MINUTES
from .providers import ModelProvider, MockProvider
from .scenario import create_demo_world
from .store import WorldStore
from .story_graph import (
    DAILY_STORY_GRAPH_VERSION,
    MAX_DAILY_CUSTOMERS,
    DailyStoryGraph,
    StoryGraphNode,
    StoryNodeKind,
)


_AGENT_IDS = ("dana", "dorothy", "alma", "stella", "sei")
_NON_NARRATIVE_EVENTS = {
    "action_rejected",
    "agent_dialogue_completed",
    "dialogue_provider_error",
    "drink_served",
    "player_scene_ack",
    "provider_error",
}
_SHIFT_PHASE_PLAYING = "playing"
_SHIFT_PHASE_SAVE_REQUIRED = "save_required"


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
        daily_story_mode: bool = False,
        prefetch_days: int = 1,
    ) -> None:
        if advance_minutes < 0 or advance_minutes > 30 * DAY_MINUTES:
            raise ValueError("advance_minutes must be between 0 and 43200")
        if prefetch_days not in {0, 1}:
            raise ValueError("prefetch_days must be 0 or 1")
        self.db_path = Path(db_path)
        self.provider_factory = provider_factory or MockProvider
        self.error_reporter = error_reporter
        self.seed = seed
        self.advance_minutes = advance_minutes
        self.daily_story_mode = daily_story_mode
        self.prefetch_days = prefetch_days
        self._lock = threading.RLock()
        self._generation_lock = threading.Lock()
        self._generation_threads: dict[int, threading.Thread] = {}

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
    def _customer(participants: tuple[str, str]) -> str:
        if participants[0] == "dana":
            return participants[1]
        return participants[0]

    @staticmethod
    def _fallback_scene(
        event: Mapping[str, Any],
        display_names: Mapping[str, str],
        current_tick: int,
        *,
        scene_id: str | None = None,
    ) -> ScenePackage:
        event_id = int(event["event_id"])
        participants = WorldSceneService._participants(event)
        customer = WorldSceneService._customer(participants)
        other = next(item for item in participants if item != customer)
        order = order_for_customer(customer, event_id)
        lines = (
            SceneLine(
                "fallback_1",
                customer,
                SPEAKER_PORTRAITS[customer],
                "neutral",
                order.display_text,
            ),
            SceneLine(
                "fallback_2",
                "jill",
                None,
                "neutral",
                f"好，{order.requested_name}。稍等。",
            ),
            SceneLine(
                "fallback_3",
                other,
                SPEAKER_PORTRAITS[other],
                "neutral",
                "看来现在轮到吧台说话了。",
            ),
        )
        return ScenePackage(scene_id or f"world_event_{event_id}", lines, order=order)

    def _generated_scene(
        self,
        event: Mapping[str, Any],
        display_names: Mapping[str, str],
        current_tick: int,
        engine: SimulationEngine,
        provider: ModelProvider,
        *,
        scene_id: str | None = None,
    ) -> ScenePackage:
        generator = getattr(provider, "generate_dialogue_line", None)
        player_generator = getattr(provider, "generate_player_dialogue_line", None)
        if not callable(generator) or not callable(player_generator):
            return self._fallback_scene(
                event, display_names, current_tick, scene_id=scene_id
            )

        event_id = int(event["event_id"])
        scene_id = scene_id or f"world_event_{event_id}"
        participants = self._participants(event)
        customer = self._customer(participants)
        other = next(item for item in participants if item != customer)
        order = order_for_customer(customer, event_id)
        public_participants = tuple(dict.fromkeys((*participants, "jill")))
        turn_count = 3
        premise = (
            self._event_premise(event, display_names, current_tick)
            + f" {display_names.get(customer, customer.title())}明确点了"
            + f"{order.requested_name}，Jill 正在吧台后确认这份点单。"
        )
        transcript = [DialogueUtterance(customer, order.display_text)]
        player_context = PlayerDialogueTurnContext(
            scene_id,
            1,
            turn_count,
            premise,
            public_participants,
            tuple(transcript),
        )
        player_proposed = player_generator(player_context)
        player_draft = validate_player_dialogue_output(
            {
                "expression_id": player_proposed.expression_id,
                "text": player_proposed.text,
            },
            player_context,
        )
        transcript.append(DialogueUtterance("jill", player_draft.text))
        agent_context = DialogueTurnContext(
            scene_id=scene_id,
            turn_index=2,
            turn_count=turn_count,
            premise=premise,
            speaker=engine.context_for_agent(current_tick, other),
            participant_ids=public_participants,
            transcript=tuple(transcript),
        )
        agent_proposed = generator(agent_context)
        agent_draft = validate_dialogue_output(
            {
                "expression_id": agent_proposed.expression_id,
                "text": agent_proposed.text,
            },
            agent_context,
        )
        lines = (
            SceneLine(
                "dialogue_1",
                customer,
                SPEAKER_PORTRAITS[customer],
                "neutral",
                order.display_text,
            ),
            SceneLine("dialogue_2", "jill", None, "neutral", player_draft.text),
            SceneLine(
                "dialogue_3",
                other,
                SPEAKER_PORTRAITS[other],
                agent_draft.expression_id,
                agent_draft.text,
            ),
        )
        return ScenePackage(scene_id, lines, order=order)

    @staticmethod
    def _result_closing(result: ServiceResult) -> str:
        return {
            ServiceCategory.EXACT: "嗯，就是这个。",
            ServiceCategory.ACCEPTABLE: "不是原来那杯，不过这个也不错。",
            ServiceCategory.WRONG: "……Jill，这杯好像不太对。",
            ServiceCategory.SPECIAL: "这个分量很有诚意。",
        }[result.category]

    @staticmethod
    def _result_premise(order: DrinkOrder, result: ServiceResult) -> str:
        meanings = {
            ServiceCategory.EXACT: "Jill准确完成了点单",
            ServiceCategory.ACCEPTABLE: "Jill做的不是原点单，但符合顾客公开偏好",
            ServiceCategory.WRONG: "Jill端出的饮品没有满足点单",
            ServiceCategory.SPECIAL: "Jill准确完成点单并做成了加大杯",
        }
        return (
            f"{order.customer_id}点了{order.requested_name}。"
            f"规则层确认端上的是{result.beverage_name}；"
            f"{meanings[result.category]}。角色只对这个既定事实作出反应。"
        )

    @staticmethod
    def _fallback_reaction(
        order: DrinkOrder,
        result: ServiceResult,
        service_event_id: int,
        *,
        scene_id: str | None = None,
    ) -> ScenePackage:
        opening = {
            ServiceCategory.EXACT: f"这杯{result.beverage_name}正合适。",
            ServiceCategory.ACCEPTABLE: f"{result.beverage_name}？和我点的不一样，不过可以试试。",
            ServiceCategory.WRONG: "这个味道不对。你是不是拿错杯子了？",
            ServiceCategory.SPECIAL: f"加大杯的{result.beverage_name}？今天这么大方？",
        }[result.category]
        jill_line = {
            ServiceCategory.EXACT: "配方没跑。慢慢喝。",
            ServiceCategory.ACCEPTABLE: "确实不是原单。你愿意的话，这杯算我的建议。",
            ServiceCategory.WRONG: "是我失手了。下一杯我重做。",
            ServiceCategory.SPECIAL: "杯子大了，配方还是那杯。",
        }[result.category]
        lines = (
            SceneLine(
                "fallback_1",
                order.customer_id,
                SPEAKER_PORTRAITS[order.customer_id],
                "neutral",
                opening,
            ),
            SceneLine("fallback_2", "jill", None, "neutral", jill_line),
            SceneLine(
                "fallback_3",
                order.customer_id,
                SPEAKER_PORTRAITS[order.customer_id],
                "neutral",
                WorldSceneService._result_closing(result),
            ),
        )
        return ScenePackage(scene_id or f"order_result_{service_event_id}", lines)

    def _generated_reaction(
        self,
        order: DrinkOrder,
        result: ServiceResult,
        service_event_id: int,
        current_tick: int,
        engine: SimulationEngine,
        provider: ModelProvider,
        *,
        scene_id: str | None = None,
    ) -> ScenePackage:
        generator = getattr(provider, "generate_dialogue_line", None)
        player_generator = getattr(provider, "generate_player_dialogue_line", None)
        if not callable(generator) or not callable(player_generator):
            return self._fallback_reaction(
                order, result, service_event_id, scene_id=scene_id
            )
        scene_id = scene_id or f"order_result_{service_event_id}"
        participants = (order.customer_id, "jill")
        premise = self._result_premise(order, result)
        customer_context = DialogueTurnContext(
            scene_id,
            0,
            3,
            premise,
            engine.context_for_agent(current_tick, order.customer_id),
            participants,
            (),
            result,
        )
        customer_proposed = generator(customer_context)
        customer_draft = validate_dialogue_output(
            {
                "expression_id": customer_proposed.expression_id,
                "text": customer_proposed.text,
            },
            customer_context,
        )
        transcript = [DialogueUtterance(order.customer_id, customer_draft.text)]
        player_context = PlayerDialogueTurnContext(
            scene_id,
            1,
            3,
            premise,
            participants,
            tuple(transcript),
            result,
        )
        player_proposed = player_generator(player_context)
        player_draft = validate_player_dialogue_output(
            {
                "expression_id": player_proposed.expression_id,
                "text": player_proposed.text,
            },
            player_context,
        )
        return ScenePackage(
            scene_id,
            (
                SceneLine(
                    "dialogue_1",
                    order.customer_id,
                    SPEAKER_PORTRAITS[order.customer_id],
                    customer_draft.expression_id,
                    customer_draft.text,
                ),
                SceneLine("dialogue_2", "jill", None, "neutral", player_draft.text),
                SceneLine(
                    "dialogue_3",
                    order.customer_id,
                    SPEAKER_PORTRAITS[order.customer_id],
                    "neutral",
                    self._result_closing(result),
                ),
            ),
        )

    @staticmethod
    def _candidate_result(
        order: DrinkOrder, category: ServiceCategory
    ) -> ServiceResult:
        alcoholic = order.alcohol_requirement is not AlcoholRequirement.FORBIDDEN
        if category in {ServiceCategory.EXACT, ServiceCategory.SPECIAL}:
            beverage_id = order.requested_drink_id
            beverage_name = order.requested_name
        elif category is ServiceCategory.ACCEPTABLE:
            beverage_id = "acceptable_alternative"
            beverage_name = "另一杯符合偏好的饮品"
        else:
            beverage_id = "incorrect_drink"
            beverage_name = "不符合点单的饮品"
        return ServiceResult(
            order.order_id,
            order.customer_id,
            category,
            beverage_id,
            beverage_name,
            alcoholic,
        )

    @staticmethod
    def _daily_source_events(
        events: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        selected: list[dict[str, Any]] = []
        customers: set[str] = set()
        for event in reversed(events):
            if event["event_type"] in _NON_NARRATIVE_EVENTS:
                continue
            customer = WorldSceneService._customer(
                WorldSceneService._participants(event)
            )
            if customer in customers:
                continue
            selected.append(event)
            customers.add(customer)
            if len(selected) == MAX_DAILY_CUSTOMERS:
                break
        selected.reverse()
        return tuple(selected)

    def _build_daily_story_graph(
        self,
        day_index: int,
        source_tick: int,
        events: tuple[dict[str, Any], ...],
        display_names: Mapping[str, str],
        engine: SimulationEngine,
        provider: ModelProvider,
    ) -> DailyStoryGraph:
        nodes: list[StoryGraphNode] = []
        for index, event in enumerate(events, start=1):
            prefix = f"day_{day_index}_customer_{index}"
            arrival_id = f"{prefix}_arrival"
            merge_id = f"{prefix}_merge"
            next_arrival_id = (
                f"day_{day_index}_customer_{index + 1}_arrival"
                if index < len(events)
                else None
            )
            topic = self._event_premise(event, display_names, source_tick)
            arrival_scene = self._generated_scene(
                event,
                display_names,
                source_tick,
                engine,
                provider,
                scene_id=f"{prefix}_order",
            )
            order = arrival_scene.order
            if order is None:
                raise ValueError("generated daily arrival did not contain an order")
            order = replace(order, order_id=f"order_day_{day_index}_{index}")
            arrival_scene = replace(arrival_scene, order=order)
            branch_targets = tuple(
                (category.value, f"{prefix}_{category.value}")
                for category in ServiceCategory
            )
            nodes.append(
                StoryGraphNode(
                    arrival_id,
                    StoryNodeKind.ARRIVAL_ORDER,
                    order.customer_id,
                    topic,
                    scene=arrival_scene,
                    branch_targets=branch_targets,
                )
            )
            for category in ServiceCategory:
                result = self._candidate_result(order, category)
                result_topic = self._result_premise(order, result)
                reaction = self._generated_reaction(
                    order,
                    result,
                    int(event["event_id"]),
                    source_tick,
                    engine,
                    provider,
                    scene_id=f"{prefix}_{category.value}",
                )
                nodes.append(
                    StoryGraphNode(
                        f"{prefix}_{category.value}",
                        StoryNodeKind.RESULT_DIALOGUE,
                        order.customer_id,
                        result_topic,
                        scene=reaction,
                        service_category=category,
                        next_node_id=merge_id,
                    )
                )
            nodes.append(
                StoryGraphNode(
                    merge_id,
                    StoryNodeKind.MERGE,
                    None,
                    "",
                    next_node_id=next_arrival_id,
                )
            )
        return DailyStoryGraph(
            f"daily_story_day_{day_index}",
            day_index,
            DAILY_STORY_GRAPH_VERSION,
            source_tick,
            tuple(int(event["event_id"]) for event in events),
            f"day_{day_index}_customer_1_arrival",
            f"day_{day_index}_customer_{len(events)}_merge",
            tuple(nodes),
        )

    def prepare_daily_story_graph(self, day_index: int) -> DailyStoryGraph:
        """Generate or replay one bounded day without committing draft effects."""

        if isinstance(day_index, bool) or not isinstance(day_index, int) or day_index < 1:
            raise ValueError("day_index must be a positive integer")
        with self._generation_lock, WorldStore(self.db_path) as store:
            record = store.get_daily_story_graph(
                day_index, DAILY_STORY_GRAPH_VERSION
            )
            if record is not None and record["status"] == "ready":
                raw_graph = record["graph"]
                if not isinstance(raw_graph, Mapping):
                    raise ValueError("ready daily story graph payload was invalid")
                graph = DailyStoryGraph.from_dict(raw_graph)
                if (
                    graph.day_index != day_index
                    or graph.generation_version != DAILY_STORY_GRAPH_VERSION
                    or graph.source_tick != record["source_tick"]
                    or graph.source_event_ids != record["source_event_ids"]
                ):
                    raise ValueError("ready daily story graph metadata was inconsistent")
                return graph

            if record is None:
                self._engine(store, MockProvider())
                source_events = self._daily_source_events(store.list_events())
                if not source_events:
                    raise ValueError("world did not contain a story source event")
                source_tick = store.current_tick
                source_event_ids = tuple(
                    int(event["event_id"]) for event in source_events
                )
            else:
                source_tick = int(record["source_tick"])
                source_event_ids = tuple(record["source_event_ids"])

            store.begin_daily_story_graph(
                day_index,
                DAILY_STORY_GRAPH_VERSION,
                source_tick,
                source_event_ids,
            )
            try:
                provider = self.provider_factory()
                engine = self._engine(store, provider)
                source_events = tuple(
                    self._find_event(store, event_id)
                    for event_id in source_event_ids
                )
                display_names = {
                    agent.agent_id: agent.display_name for agent in store.list_agents()
                }
                graph = self._build_daily_story_graph(
                    day_index,
                    source_tick,
                    source_events,
                    display_names,
                    engine,
                    provider,
                )
                store.complete_daily_story_graph(
                    day_index,
                    DAILY_STORY_GRAPH_VERSION,
                    graph.to_dict(),
                )
                return graph
            except Exception as exc:
                self._report_error("daily story graph generation", exc)
                store.fail_daily_story_graph(
                    day_index,
                    DAILY_STORY_GRAPH_VERSION,
                    type(exc).__name__,
                )
                raise

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
            if line.speaker_id is not None
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
        speakers = tuple(dict.fromkeys(line.speaker_id for line in scene.lines))
        participants = tuple(item for item in speakers if item in _AGENT_IDS)
        if not participants:
            return
        display_names = {
            agent.agent_id: agent.display_name for agent in store.list_agents()
        }
        summary = WorldSceneService._dialogue_memory_summary(scene, display_names)
        memory_event_id = store.append_event(
            store.current_tick,
            "agent_dialogue_completed",
            participants[0],
            participants[1] if len(participants) > 1 else None,
            {
                "scene_id": scene.scene_id,
                "source_event_id": source_event_id,
                "participants": list(speakers),
                "summary": summary,
            },
        )
        tags = {"dialogue", "va11_hall_a", *speakers}
        for participant_id in participants:
            store.append_memory(
                participant_id,
                memory_event_id,
                0.75,
                summary,
                tags,
            )

    @staticmethod
    def _story_node(graph: DailyStoryGraph, node_id: str) -> StoryGraphNode:
        node = next((item for item in graph.nodes if item.node_id == node_id), None)
        if node is None:
            raise ValueError("daily story cursor referenced a missing node")
        return node

    @staticmethod
    def _story_source_event_id(
        graph: DailyStoryGraph, node: StoryGraphNode
    ) -> int:
        arrivals = [
            item for item in graph.nodes if item.kind is StoryNodeKind.ARRIVAL_ORDER
        ]
        for index, arrival in enumerate(arrivals):
            if arrival.node_id == node.node_id:
                return graph.source_event_ids[index]
        raise ValueError("daily story arrival did not have a source event")

    @staticmethod
    def _ambient_scene(day_index: int, kind: str) -> ScenePackage:
        if kind == "opening":
            texts = (
                "冰箱压缩机在吧台后低声运转。",
                "雨点断断续续敲着窗。",
                "洗净的酒杯在灯下慢慢晾干。",
            )
        elif kind == "doorbell":
            texts = ("门铃响了。",)
        elif kind == "closing":
            texts = ("最后一位客人离开后，门铃安静了下来。",)
        else:
            texts = ("酒馆暂时安静了下来。",)
        return ScenePackage(
            f"{kind}_day_{day_index}",
            tuple(
                SceneLine(f"ambient_{index}", None, None, "neutral", text)
                for index, text in enumerate(texts, start=1)
            ),
        )

    @staticmethod
    def _settlement_scene(day_index: int, income: int) -> ScenePackage:
        return ScenePackage(
            f"settlement_day_{day_index}",
            (
                SceneLine(
                    "settlement_close",
                    None,
                    None,
                    "neutral",
                    f"Jill 收好最后一只杯子。今晚的营业收入是 ¥{income}。",
                ),
                SceneLine(
                    "settlement_home",
                    None,
                    None,
                    "neutral",
                    "回到家后，Jill 还可以看看当天的新闻，再整理一下房间。",
                ),
                SceneLine(
                    "settlement_save",
                    None,
                    None,
                    "neutral",
                    "准备好后，在平板中存档；新的营业日会在保存成功后开始。",
                ),
            ),
        )

    @staticmethod
    def _save_required_scene(completed_day: int) -> ScenePackage:
        return ScenePackage(
            f"save_required_day_{completed_day}",
            (
                SceneLine(
                    "save_required",
                    None,
                    None,
                    "neutral",
                    "这一天已经结束。Jill 需要先保存记录，再开始新的营业日。",
                ),
            ),
        )

    def _start_daily_story_generation(self, day_index: int) -> None:
        existing = self._generation_threads.get(day_index)
        if existing is not None and existing.is_alive():
            return

        def generate() -> None:
            try:
                self.prepare_daily_story_graph(day_index)
            except Exception:
                return

        worker = threading.Thread(
            target=generate,
            name=f"open-shift-story-day-{day_index}",
            daemon=True,
        )
        self._generation_threads[day_index] = worker
        worker.start()

    def wait_for_background_generation(self, timeout_seconds: float = 5.0) -> None:
        """Wait for currently scheduled graph workers, primarily for clean shutdown."""

        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        deadline = time.monotonic() + timeout_seconds
        for worker in tuple(self._generation_threads.values()):
            remaining = max(0.0, deadline - time.monotonic())
            worker.join(remaining)

    @staticmethod
    def _persist_ambient_request(
        store: WorldStore,
        request: Mapping[str, Any],
        scene: ScenePackage,
    ) -> ScenePackage:
        request_id = str(request["request_id"])
        request_json = json.dumps(
            dict(request), separators=(",", ":"), sort_keys=True
        )
        meta_key = f"bridge_open:{request_id}"
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
        with store.transaction():
            store.set_meta(
                meta_key,
                json.dumps(
                    {"dialogue_version": 3, "request": request_json, "scene": scene.to_dict()},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            store.set_meta(f"bridge_scene:{scene.scene_id}", "ambient")
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

    def _open_daily_story_scene(
        self, request: Mapping[str, Any]
    ) -> ScenePackage:
        with WorldStore(self.db_path) as day_store:
            day_index = int(day_store.get_meta("current_story_day", "1") or 1)
        request_id = str(request["request_id"])
        request_json = json.dumps(
            dict(request), separators=(",", ":"), sort_keys=True
        )
        with self._lock, WorldStore(self.db_path) as store:
            if store.get_meta("shift_phase", _SHIFT_PHASE_PLAYING) == _SHIFT_PHASE_SAVE_REQUIRED:
                completed_day = int(
                    store.get_meta("last_completed_story_day", str(max(1, day_index - 1)))
                    or max(1, day_index - 1)
                )
                return self._persist_ambient_request(
                    store, request, self._save_required_scene(completed_day)
                )
            graph_record = store.get_daily_story_graph(
                day_index, DAILY_STORY_GRAPH_VERSION
            )
            opening = self._ambient_scene(day_index, "opening")
            if store.get_meta(f"bridge_ack:{opening.scene_id}") is None:
                if graph_record is None:
                    self._start_daily_story_generation(day_index)
                return self._persist_ambient_request(store, request, opening)
            if graph_record is None:
                self._start_daily_story_generation(day_index)
                return self._persist_ambient_request(
                    store, request, self._ambient_scene(day_index, "waiting")
                )
            if graph_record["status"] == "generating":
                return self._persist_ambient_request(
                    store, request, self._ambient_scene(day_index, "waiting")
                )
            if graph_record["status"] == "failed":
                report_key = (
                    f"story_failure_reported:{day_index}:"
                    f"{graph_record['attempt_count']}"
                )
                if store.get_meta(report_key) is None:
                    store.set_meta(report_key, "1")
                    raise BridgeError(
                        503,
                        "story_generation_failed",
                        f"daily story generation failed: {graph_record['error_code']}",
                    )
                self._start_daily_story_generation(day_index)
                return self._persist_ambient_request(
                    store, request, self._ambient_scene(day_index, "waiting")
                )
            raw_graph = graph_record["graph"]
            if not isinstance(raw_graph, Mapping):
                raise ValueError("ready daily story graph payload was invalid")
            graph = DailyStoryGraph.from_dict(raw_graph)
            if self.prefetch_days == 1:
                self._start_daily_story_generation(day_index + 1)
            doorbell = self._ambient_scene(day_index, "doorbell")
            if store.get_meta(f"bridge_ack:{doorbell.scene_id}") is None:
                return self._persist_ambient_request(store, request, doorbell)
            meta_key = f"bridge_open:{request_id}"
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

            progress = store.get_daily_story_progress(
                day_index, DAILY_STORY_GRAPH_VERSION
            )
            if progress is None:
                raise ValueError("daily story progress was missing")
            if progress["status"] == "completed":
                closing = self._ambient_scene(day_index, "closing")
                if store.get_meta(f"bridge_ack:{closing.scene_id}") is None:
                    return self._persist_ambient_request(store, request, closing)
                income = int(store.get_meta("player_shift_income", "0") or 0)
                return self._persist_ambient_request(
                    store, request, self._settlement_scene(day_index, income)
                )
            node = self._story_node(graph, str(progress["current_node_id"]))
            if node.kind not in {
                StoryNodeKind.ARRIVAL_ORDER,
                StoryNodeKind.RESULT_DIALOGUE,
            } or node.scene is None:
                raise ValueError("daily story cursor did not reference a playable scene")
            if node.kind is StoryNodeKind.ARRIVAL_ORDER:
                source_event_id = self._story_source_event_id(graph, node)
            else:
                commit = next(
                    (
                        item
                        for item in store.list_story_branch_commits()
                        if item["result_node_id"] == node.node_id
                    ),
                    None,
                )
                if commit is None:
                    raise ValueError("daily result node had no committed branch")
                source_event_id = int(commit["service_event_id"])
            scene = node.scene
            record = {
                "dialogue_version": 3,
                "request": request_json,
                "scene": scene.to_dict(),
                "story_day": day_index,
                "story_node_id": node.node_id,
            }
            with store.transaction():
                store.set_meta(
                    meta_key,
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
                store.set_meta(f"bridge_scene:{scene.scene_id}", source_event_id)
                store.set_meta(
                    f"bridge_scene_payload:{scene.scene_id}",
                    json.dumps(
                        scene.to_dict(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
                store.set_meta(
                    f"story_scene_node:{scene.scene_id}",
                    json.dumps(
                        {"day_index": day_index, "node_id": node.node_id},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            return scene

    def open_scene(self, request: Mapping[str, Any]) -> ScenePackage:
        if self.daily_story_mode:
            return self._open_daily_story_scene(request)
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
                            payload={"source": "stage_6_bridge"},
                        )
                        events = [store.list_events()[-1]]
                    event = events[-1]
                    issue_tick = store.current_tick
                    record = {
                        "dialogue_version": 2,
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

    def _resolve_daily_story_order(
        self,
        store: WorldStore,
        request: Mapping[str, Any],
        source_scene: ScenePackage,
        submission: DrinkSubmission,
        normalized_drink: Mapping[str, Any],
        request_json: str,
        resolution_input: str,
        story_reference: Mapping[str, Any],
    ) -> OrderResolution:
        order = source_scene.order
        if order is None:
            raise KeyError("story scene did not contain an order")
        day_index = int(story_reference["day_index"])
        arrival_node_id = str(story_reference["node_id"])
        graph_record = store.get_daily_story_graph(
            day_index, DAILY_STORY_GRAPH_VERSION
        )
        if graph_record is None or not isinstance(graph_record["graph"], Mapping):
            raise ValueError("daily story graph was unavailable during order resolution")
        graph = DailyStoryGraph.from_dict(graph_record["graph"])
        arrival = self._story_node(graph, arrival_node_id)
        if arrival.kind is not StoryNodeKind.ARRIVAL_ORDER:
            raise ValueError("story order did not reference an arrival node")
        progress = store.get_daily_story_progress(
            day_index, DAILY_STORY_GRAPH_VERSION
        )
        existing_commit = store.get_story_branch_commit(order.order_id)
        if progress is None or (
            progress["current_node_id"] != arrival_node_id
            and existing_commit is None
        ):
            raise BridgeError(
                409,
                "story_branch_already_advanced",
                "the daily story had already advanced past this order",
            )

        request_key = f"bridge_order_request:{request['request_id']}"
        order_key = f"bridge_order:{order.order_id}"
        with store.transaction():
            prior_request = store.get_meta(request_key)
            if prior_request is not None and prior_request != request_json:
                raise BridgeError(
                    409,
                    "request_id_conflict",
                    "request_id was already used with different content",
                )
            prior_order = store.get_meta(order_key)
            if prior_order is not None:
                record = json.loads(prior_order)
                if record.get("resolution_input") != resolution_input:
                    raise BridgeError(
                        409,
                        "order_already_resolved",
                        "the order was already resolved with a different drink",
                    )
                store.set_meta(request_key, request_json)
                return OrderResolution(
                    ServiceResult.from_dict(record["result"]),
                    ScenePackage.from_dict(record["scene"]),
                    int(record["income_delta"]),
                )

            result = evaluate_service(order, submission)
            result_node_id = dict(arrival.branch_targets)[result.category.value]
            result_node = self._story_node(graph, result_node_id)
            if result_node.scene is None:
                raise ValueError("selected daily result branch had no scene")
            income_delta = service_income(result, submission)
            service_event_id = store.append_event(
                store.current_tick,
                "drink_served",
                order.customer_id,
                payload={
                    "source_scene_id": source_scene.scene_id,
                    "source_event_id": self._story_source_event_id(graph, arrival),
                    "story_day": day_index,
                    "story_node_id": result_node_id,
                    "order": order.to_dict(),
                    "drink": dict(normalized_drink),
                    "result": result.to_dict(),
                    "income_delta": income_delta,
                },
            )
            store.record_story_branch_commit(
                day_index=day_index,
                generation_version=DAILY_STORY_GRAPH_VERSION,
                order_id=order.order_id,
                arrival_node_id=arrival_node_id,
                result_node_id=result_node_id,
                category=result.category.value,
                service_event_id=service_event_id,
                income_delta=income_delta,
            )
            store.advance_daily_story_cursor(
                day_index,
                DAILY_STORY_GRAPH_VERSION,
                arrival_node_id,
                result_node_id,
            )
            total_income = int(store.get_meta("player_shift_income", "0") or 0)
            store.set_meta("player_shift_income", total_income + income_delta)
            record = {
                "resolution_input": resolution_input,
                "service_event_id": service_event_id,
                "result": result.to_dict(),
                "scene": result_node.scene.to_dict(),
                "income_delta": income_delta,
            }
            store.set_meta(
                order_key,
                json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            store.set_meta(request_key, request_json)
            store.set_meta(
                f"bridge_scene:{result_node.scene.scene_id}", service_event_id
            )
            store.set_meta(
                f"bridge_scene_payload:{result_node.scene.scene_id}",
                json.dumps(
                    result_node.scene.to_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            store.set_meta(
                f"story_scene_node:{result_node.scene.scene_id}",
                json.dumps(
                    {"day_index": day_index, "node_id": result_node_id},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        return OrderResolution(result, result_node.scene, income_delta)

    def resolve_order(self, request: Mapping[str, Any]) -> OrderResolution:
        scene_id = str(request["scene_id"])
        order_id = str(request["order_id"])
        raw_drink = request["drink"]
        if not isinstance(raw_drink, Mapping):
            raise ValueError("drink must be an object")
        submission = DrinkSubmission.from_dict(raw_drink)
        normalized_drink = submission.to_dict()
        request_json = json.dumps(
            dict(request), separators=(",", ":"), sort_keys=True
        )
        resolution_input = json.dumps(
            {
                "scene_id": scene_id,
                "order_id": order_id,
                "drink": normalized_drink,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._lock, WorldStore(self.db_path) as store:
            source_event = store.get_meta(f"bridge_scene:{scene_id}")
            try:
                source_event_id = int(source_event or "")
            except ValueError:
                raise KeyError("scene_id was not issued by the bridge") from None
            scene_payload = store.get_meta(f"bridge_scene_payload:{scene_id}")
            if scene_payload is None:
                raise KeyError("scene payload was not issued by the bridge")
            source_scene = ScenePackage.from_dict(json.loads(scene_payload))
            order = source_scene.order
            if order is None or order.order_id != order_id:
                raise KeyError("order_id did not belong to the scene")
            if store.get_meta(f"bridge_ack:{scene_id}") is None:
                raise BridgeError(
                    409,
                    "scene_not_acknowledged",
                    "the order scene must be acknowledged before serving a drink",
                )
            story_reference_json = store.get_meta(f"story_scene_node:{scene_id}")
            if story_reference_json is not None:
                story_reference = json.loads(story_reference_json)
                if not isinstance(story_reference, Mapping):
                    raise ValueError("story scene reference was invalid")
                return self._resolve_daily_story_order(
                    store,
                    request,
                    source_scene,
                    submission,
                    normalized_drink,
                    request_json,
                    resolution_input,
                    story_reference,
                )

            request_key = f"bridge_order_request:{request['request_id']}"
            order_key = f"bridge_order:{order_id}"
            with store.transaction():
                prior_request = store.get_meta(request_key)
                if prior_request is not None and prior_request != request_json:
                    raise BridgeError(
                        409,
                        "request_id_conflict",
                        "request_id was already used with different content",
                    )
                prior_order = store.get_meta(order_key)
                if prior_order is not None:
                    record = json.loads(prior_order)
                    if record.get("resolution_input") != resolution_input:
                        raise BridgeError(
                            409,
                            "order_already_resolved",
                            "the order was already resolved with a different drink",
                        )
                    result = ServiceResult.from_dict(record["result"])
                    service_event_id = int(record["service_event_id"])
                    persisted_scene = record.get("scene")
                    store.set_meta(request_key, request_json)
                    if isinstance(persisted_scene, dict):
                        return OrderResolution(
                            result, ScenePackage.from_dict(persisted_scene)
                        )
                else:
                    result = evaluate_service(order, submission)
                    service_event_id = store.append_event(
                        store.current_tick,
                        "drink_served",
                        order.customer_id,
                        payload={
                            "source_scene_id": scene_id,
                            "source_event_id": source_event_id,
                            "order": order.to_dict(),
                            "drink": normalized_drink,
                            "result": result.to_dict(),
                        },
                    )
                    record = {
                        "resolution_input": resolution_input,
                        "service_event_id": service_event_id,
                        "result": result.to_dict(),
                        "scene": None,
                    }
                    store.set_meta(
                        order_key,
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    )
                    store.set_meta(request_key, request_json)

            provider = self.provider_factory()
            engine = self._engine(store, provider)
            try:
                reaction = self._generated_reaction(
                    order,
                    result,
                    service_event_id,
                    store.current_tick,
                    engine,
                    provider,
                )
            except Exception as exc:
                self._report_error("drink reaction generation", exc)
                store.append_event(
                    store.current_tick,
                    "dialogue_provider_error",
                    order.customer_id,
                    payload={
                        "error_type": type(exc).__name__,
                        "source_event_id": service_event_id,
                    },
                )
                reaction = self._fallback_reaction(order, result, service_event_id)

            with store.transaction():
                latest = json.loads(store.get_meta(order_key) or "{}")
                if latest.get("resolution_input") != resolution_input:
                    raise BridgeError(
                        409,
                        "order_already_resolved",
                        "the order resolution changed while generating dialogue",
                    )
                persisted_scene = latest.get("scene")
                if isinstance(persisted_scene, dict):
                    return OrderResolution(
                        result, ScenePackage.from_dict(persisted_scene)
                    )
                latest["scene"] = reaction.to_dict()
                store.set_meta(
                    order_key,
                    json.dumps(
                        latest,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
                store.set_meta(
                    f"bridge_scene:{reaction.scene_id}", service_event_id
                )
                store.set_meta(
                    f"bridge_scene_payload:{reaction.scene_id}",
                    json.dumps(
                        reaction.to_dict(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            return OrderResolution(result, reaction)

    def ack_scene(self, request: Mapping[str, Any]) -> None:
        scene_id = str(request["scene_id"])
        with self._lock, WorldStore(self.db_path) as store:
            issued = store.get_meta(f"bridge_scene:{scene_id}")
            ambient = issued == "ambient"
            if ambient:
                event_id = 0
            else:
                try:
                    event_id = int(issued or "")
                except ValueError:
                    raise KeyError("scene_id was not issued by the bridge") from None
                exists = any(
                    event["event_id"] == event_id for event in store.list_events()
                )
                if not exists:
                    raise KeyError("scene_id was not issued by the bridge")
            scene_payload = store.get_meta(f"bridge_scene_payload:{scene_id}")
            if scene_payload is None:
                raise KeyError("scene payload was not issued by the bridge")
            scene = ScenePackage.from_dict(json.loads(scene_payload))
            expected_outcome = (
                "order_started" if scene.order is not None else "continued_in_bar"
            )
            if request["outcome"] != expected_outcome:
                raise BridgeError(
                    409,
                    "scene_outcome_mismatch",
                    "scene acknowledgement did not match its interaction",
                )
            ack_key = f"bridge_ack:{scene_id}"
            request_key = f"bridge_ack_request:{request['request_id']}"
            request_json = json.dumps(
                dict(request), separators=(",", ":"), sort_keys=True
            )
            story_reference_json = store.get_meta(
                f"story_scene_node:{scene_id}"
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
                if story_reference_json is not None:
                    story_reference = json.loads(story_reference_json)
                    if not isinstance(story_reference, Mapping):
                        raise ValueError("story scene reference was invalid")
                    day_index = int(story_reference["day_index"])
                    node_id = str(story_reference["node_id"])
                    graph_record = store.get_daily_story_graph(
                        day_index, DAILY_STORY_GRAPH_VERSION
                    )
                    if graph_record is None or not isinstance(
                        graph_record["graph"], Mapping
                    ):
                        raise ValueError("daily story graph was missing during ack")
                    graph = DailyStoryGraph.from_dict(graph_record["graph"])
                    node = self._story_node(graph, node_id)
                    if node.kind is StoryNodeKind.RESULT_DIALOGUE:
                        merge = self._story_node(graph, str(node.next_node_id))
                        store.advance_daily_story_cursor(
                            day_index,
                            DAILY_STORY_GRAPH_VERSION,
                            node_id,
                            merge.next_node_id,
                        )
                if scene_id.startswith("settlement_day_"):
                    try:
                        completed_day = int(scene_id.removeprefix("settlement_day_"))
                    except ValueError:
                        raise ValueError("settlement scene day was invalid") from None
                    current_day = int(store.get_meta("current_story_day", "1") or 1)
                    if completed_day != current_day:
                        raise BridgeError(
                            409,
                            "story_day_mismatch",
                            "the settlement did not match the current story day",
                        )
                    income = int(store.get_meta("player_shift_income", "0") or 0)
                    store.set_meta(f"player_shift_income_day_{completed_day}", income)
                    store.set_meta("player_shift_income", 0)
                    store.set_meta("last_completed_story_day", completed_day)
                    store.set_meta("current_story_day", completed_day + 1)
                    store.set_meta("shift_phase", _SHIFT_PHASE_SAVE_REQUIRED)
                    store.set_current_tick(store.current_tick + DAY_MINUTES)
                    store.append_event(
                        store.current_tick,
                        "player_shift_completed",
                        None,
                        payload={
                            "story_day": completed_day,
                            "income": income,
                            "next_story_day": completed_day + 1,
                        },
                    )
                if not ambient:
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

    def complete_paired_save(
        self, world_day: int, last_completed_story_day: int
    ) -> None:
        """Release an end-of-day checkpoint after its paired snapshot is durable."""

        with self._lock, WorldStore(self.db_path) as store, store.transaction():
            current_day = int(store.get_meta("current_story_day", "1") or 1)
            completed_day = int(store.get_meta("last_completed_story_day", "0") or 0)
            if (
                store.get_meta("shift_phase", _SHIFT_PHASE_PLAYING)
                == _SHIFT_PHASE_SAVE_REQUIRED
                and current_day == world_day
                and completed_day == last_completed_story_day
            ):
                store.set_meta("shift_phase", _SHIFT_PHASE_PLAYING)
