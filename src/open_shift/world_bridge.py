"""Bridge adapter that exposes persistent Agent world events as safe scenes."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .bridge import (
    BridgeError,
    OrderResolution,
    SPEAKER_PORTRAITS,
    SceneLine,
    ScenePackage,
)
from .byok import BYOKBudgetExceeded, BYOKError
from .dialogue import (
    DialogueTurnContext,
    DialogueUtterance,
    PlayerDialogueTurnContext,
    SceneDirection,
    validate_dialogue_output,
    validate_player_dialogue_output,
)
from .diagnostics import emit_dialogue_transcript, emit_timing, monotonic_seconds
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
from .world_events import (
    CODE_OWNED_DAY_ONE_EVENTS,
    PublicWorldEvent,
    character_story_arcs_for_day,
    character_story_event,
    tablet_feed_item,
)
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
    "dialogue_provider_fallback",
    "dialogue_validation_fallback",
    "dialogue_transcript",
    "drink_served",
    "player_scene_ack",
    "provider_error",
}
# Only events with a user-facing social or public-world meaning may become
# customer scenes.  Engine bookkeeping and lifecycle events are intentionally
# excluded even when they happen to have an actor and target.
_NARRATIVE_EVENT_TYPES = frozenset(
    {
        "public_world_event",
        "character_story_stage",
        "worked",
        "rested",
        "travelled",
        "message_sent",
        "talked",
        "bar_visited",
        "invitation_created",
        "invitation_kept",
        "invitation_declined",
        "promise_made",
        "promise_fulfilled",
        "promise_broken",
        "story_arc_resolved",
        "goal_completed",
    }
)
_SHIFT_PHASE_PLAYING = "playing"
_SHIFT_PHASE_SAVE_REQUIRED = "save_required"


@dataclass(frozen=True, slots=True)
class NarrativePerspective:
    """A character-facing view of an event used by dialogue generation."""

    event_topic: str
    personal_stake: str
    unresolved_question: str
    short_topic: str
    anchor: str

    def __post_init__(self) -> None:
        fields = (
            self.event_topic,
            self.personal_stake,
            self.unresolved_question,
            self.short_topic,
            self.anchor,
        )
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise ValueError("narrative perspective fields were invalid")
        if any(len(value) > 240 for value in fields):
            raise ValueError("narrative perspective field was too long")

_SCHEDULED_PUBLIC_EVENTS: dict[int, tuple[PublicWorldEvent, ...]] = {
    2: (PublicWorldEvent(
        "city_transit_day_2",
        "city",
        "developing",
        "市中心交通线路临时调整",
        "施工封闭让两条常用线路绕开酒吧附近街区，预计几天内逐步恢复。",
        ("alma", "stella"),
    ),),
    4: (PublicWorldEvent(
        "apollo_trust_day_4",
        "economy",
        "active",
        "Apollo Trust 发布新的账户审查通知",
        "银行要求部分客户重新确认身份资料，街区里的小商户开始讨论影响。",
        ("dana", "sei"),
    ),),
    7: (PublicWorldEvent(
        "lilim_health_day_7",
        "health",
        "developing",
        "诊所报告纳米机排斥反应增加",
        "几家诊所同时提醒居民留意新一批症状，官方仍在核对原因。",
        ("dorothy", "alma"),
    ),),
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
        daily_story_mode: bool = False,
        prefetch_days: int = 0,
        allow_provider_fallback: bool = True,
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
        self.allow_provider_fallback = allow_provider_fallback
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

    @staticmethod
    def _story_day_for_scene(scene_id: str) -> int:
        # Scene ids use both ``day_1_customer_1_order`` and
        # ``pre_opening_day_1``-style prefixes.  Keep the parser anchored to
        # the day token so a customer/order number cannot be mistaken for a
        # story day, while still accepting a day token at the beginning.
        match = re.search(r"(?:^|_)day_(\d+)(?:_|$)", str(scene_id or ""))
        if match is None:
            return 0
        return int(match.group(1))

    def _engine(
        self, store: WorldStore, provider: ModelProvider
    ) -> SimulationEngine:
        return create_demo_world(store, provider, seed=self.seed)

    @staticmethod
    def _participants(event: Mapping[str, Any]) -> tuple[str, str]:
        event_id = int(event["event_id"])
        raw_actor = event.get("actor_id")
        payload = event.get("payload")
        affected_agents = (
            payload.get("affected_agents")
            if isinstance(payload, Mapping)
            else event.get("affected_agents")
        )
        if raw_actor not in _AGENT_IDS and isinstance(affected_agents, (list, tuple)):
            affected = [item for item in affected_agents if item in _AGENT_IDS]
            if affected:
                raw_actor = affected[0]
        actor = raw_actor if raw_actor in _AGENT_IDS else "dana"
        raw_target = event.get("target_id")
        if raw_target not in _AGENT_IDS and isinstance(affected_agents, (list, tuple)):
            affected = [item for item in affected_agents if item in _AGENT_IDS and item != actor]
            if affected:
                raw_target = affected[0]
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
        payload = event.get("payload")
        details = payload if isinstance(payload, Mapping) else {}

        def safe_text(*keys: str, fallback: str = "") -> str:
            """Return a short human-facing payload field, never an internal id."""

            for key in keys:
                value = details.get(key)
                if not isinstance(value, str):
                    continue
                candidate = " ".join(value.split()).strip()
                # Internal identifiers are deliberately not passed through to
                # prompts or dialogue.  Reject the whole field if it contains
                # a snake_case implementation token rather than trying to
                # make a partial identifier sound conversational.
                if not candidate or re.search(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", candidate):
                    continue
                return candidate[:96].rstrip()
            return fallback

        def safe_int(key: str, fallback: int) -> int:
            try:
                value = int(details.get(key, fallback))
            except (TypeError, ValueError):
                return fallback
            return value

        if event_type == "public_world_event":
            if details:
                headline = str(details.get("headline", "城市出现了新的公开消息。"))
                summary = str(details.get("summary", ""))
                return f"{headline}。{summary}".strip()
        if event_type == "character_story_stage":
            facts = safe_text("facts", "summary", fallback="一件还没有结论的事")
            return facts
        templates = {
            "worked": f"{actor}刚结束工作并拿到{safe_int('wage', 120)}信用点，来酒吧向{target}说起这笔收入打算怎么用。",
            "rested": f"{actor}在家休息了{safe_int('duration_minutes', 60)}分钟后遇到{target}，正在考虑今晚要不要改变原定安排。",
            "travelled": f"{actor}从{safe_text('location', fallback='市中心')}赶到酒吧，在吧台碰见{target}，需要解释为什么临时改了路线。",
            "message_sent": f"{actor}刚给{target}发了一条消息，今晚想当面确认对方是否会按消息里的安排行动。",
            "talked": f"{actor}和{target}正在核对上一轮谈话里提到的具体安排，双方还没有确认谁先行动。",
            "bar_visited": f"{actor}来到仍在营业的 VA-11 Hall-A，正好遇见{target}。",
            "invitation_created": f"{actor}刚邀请{target}去{safe_text('location', fallback='酒吧附近')}见面，今晚想确认对方是否答应。",
            "invitation_kept": f"{actor}和{target}按约在{safe_text('location', fallback='酒吧')}碰面，需要决定接下来是否继续这次安排。",
            "invitation_declined": f"{actor}和{target}正在处理一次被拒绝的见面邀请，以及它对双方关系的影响。",
            "promise_made": f"{actor}答应在{safe_int('due_tick', current_tick + 60)}前为{target}做一件事，今晚想确认具体期限。",
            "promise_fulfilled": f"{actor}已经完成答应{target}的安排，正在说明结果和下一步。",
            "promise_broken": f"{actor}没有完成答应{target}的安排，今晚必须解释失约原因。",
            "story_arc_resolved": f"{actor}和{target}刚把一段关系中的进展推进到结果，正在讨论是否继续联系。",
            # goal_id is an internal key; a public goal label may be used if
            # supplied, otherwise keep the spoken premise generic.
            "goal_completed": f"{actor}完成了{safe_text('goal_name', 'goal_label', fallback='工作目标')}，今晚要告诉{target}这会怎样改变下一步计划。",
            "goal_created": f"{actor}设定了{safe_text('goal_name', 'goal_label', fallback='新的工作目标')}，想听{target}对执行办法的看法。",
        }
        if event_type in templates:
            return templates[event_type]

        # Unknown event types are implementation details, not dialogue. Use
        # an explicitly public field when one exists, otherwise describe the
        # social situation without exposing the event key.
        detail = safe_text(
            "headline", "summary", "subject", "description", "detail", "location"
        )
        if detail:
            return f"{actor}想和{target}当面确认一件事：{detail}。"
        return f"{actor}和{target}刚碰到一件需要当面确认的事，今晚想把话说清楚。"

    @classmethod
    def _narrative_perspective(
        cls,
        event: Mapping[str, Any],
        customer: str,
        display_names: Mapping[str, str],
        current_tick: int,
    ) -> NarrativePerspective:
        """Translate world state into the customer's immediate problem.

        The public event is useful as canon, but its headline is not dialogue.
        This layer gives each customer a concrete stake and a decision they
        can actually bring to the bar.
        """

        name = display_names.get(customer, customer.title())
        event_type = str(event.get("event_type", "world_event"))
        raw_payload = event.get("payload")
        details = raw_payload if isinstance(raw_payload, Mapping) else event
        event_key = str(details.get("event_key", ""))
        if event_type == "character_story_stage":
            facts = cls._clean_story_text(details.get("facts"), "一件还没有说完的事")
            stake = cls._clean_story_text(details.get("stake"), "这件事已经影响到今晚的安排。")
            stance = cls._clean_story_text(details.get("stance"), "我还没想好该怎么处理。")
            question = cls._clean_story_text(details.get("choice"), "现在要不要先做个决定？")
            follow_up = cls._clean_story_text(details.get("follow_up"), "这件事还会继续影响之后的安排。")
            title = cls._clean_story_text(details.get("title"), "今晚要谈的事")
            return NarrativePerspective(
                facts,
                f"{stake} {stance}",
                f"{question} 后续：{follow_up}",
                title,
                cls._short_event_topic(title, 18),
            )
        day_one = {
            ("alma", "city_news_day_1_transit"): (
                "明早去见客户的路线临时改了，我得在改期和绕远路之间选一个。",
                "这会不会让客户以为我又迟到了。",
                "明早到底改路线，还是干脆把见面往后挪？",
                "路线",
            ),
            ("sei", "city_news_day_1_night_market"): (
                "夜市重新开了几摊，明晚接人的路线上会多出一大段人流。",
                "人一多，护送就不能再照原来的路线走。",
                "我是换一条路，还是提前出发避开人群？",
                "夜市",
            ),
            ("dorothy", "city_news_day_1_weather"): (
                "雨停得比预报早，可我原定的约见已经被积水拖延了。",
                "再改一次时间，可能就会让对方觉得我在敷衍。",
                "这次约见要不要恢复，还是继续往后推？",
                "约见",
            ),
        }
        selected = day_one.get((customer, event_key))
        if selected is not None:
            topic, stake, question, anchor = selected
            short_topics = {
                "alma": "明早见客户的安排",
                "sei": "明晚那条接人路线",
                "dorothy": "那场被积水耽误的约见",
            }
            return NarrativePerspective(
                topic, stake, question, short_topics.get(customer, anchor), anchor
            )

        category = str(details.get("category", "local"))
        category_topics = {
            "city": ("城里的交通变化已经影响到我的安排", "改路线还是改时间"),
            "security": ("最近的安保变化让我不能照原计划行动", "冒险照旧，还是先换个方案"),
            "technology": ("这次技术故障正好卡在我的工作安排上", "等它恢复，还是先找替代办法"),
            "health": ("这条健康消息让我得重新安排一次见面", "照原计划去，还是先推迟"),
            "economy": ("这阵子的生意变化已经碰到我的收入安排", "先保住眼前的事，还是押下一步"),
            "culture": ("这件城里的新鲜事打乱了我原来的约定", "顺着变化走，还是按原计划"),
            "local": ("今天的变化正好撞上我手头的一件事", "现在处理，还是等情况明朗一点"),
        }
        base, choice = category_topics.get(category, category_topics["local"])
        if event_type != "public_world_event":
            topic = cls._event_premise(event, display_names, current_tick)
            topic = f"{name}得处理一件已经影响到自己安排的事：{cls._short_event_topic(topic, 48)}。"
        else:
            headline = cls._short_event_topic(str(details.get("headline", "")), 24)
            if category == "city" and headline:
                topic = f"{headline}已经打乱我的安排，今晚得在改路线和改期之间选一个。"
            else:
                topic = f"{base}，今晚得先决定下一步。"
        stake = f"这件事已经影响到{name}今晚的安排，不能只当成闲聊。"
        question = f"{choice}？"
        anchor = cls._short_event_topic(topic, 18)
        return NarrativePerspective(topic, stake, question, cls._short_event_topic(topic, 32), anchor)

    @staticmethod
    def _clean_story_text(value: Any, fallback: str) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text or re.search(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", text):
            return fallback
        return text[:240]

    @classmethod
    def _perspective_for_event(
        cls, event: Mapping[str, Any], display_names: Mapping[str, str], current_tick: int
    ) -> NarrativePerspective:
        return cls._narrative_perspective(
            event,
            cls._customer(cls._participants(event)),
            display_names,
            current_tick,
        )

    @staticmethod
    def _record_scene_validation_fallback(
        store: WorldStore,
        *,
        scene_id: str,
        source_event_id: int,
        story_day: int,
        reason: str,
    ) -> None:
        """Persist only safe metadata when generated dialogue is rejected."""

        store.append_event(
            store.current_tick,
            "dialogue_validation_fallback",
            None,
            payload={
                "scene_id": scene_id,
                "reason": reason,
                "source_event_id": source_event_id,
                "story_day": story_day,
            },
        )

    def publish_public_world_event(self, event: PublicWorldEvent) -> int:
        """Persist one event idempotently for both dialogue and tablet use."""

        payload_json = json.dumps(
            event.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        with self._lock, WorldStore(self.db_path) as store:
            meta_key = f"public_world_event:{event.event_key}"
            prior = store.get_meta(meta_key)
            if prior is not None:
                record = json.loads(prior)
                if record.get("payload") != payload_json:
                    raise BridgeError(
                        409,
                        "world_event_conflict",
                        "world event key was already used with different content",
                    )
                return int(record["event_id"])
            with store.transaction():
                event_id = store.append_event(
                    store.current_tick,
                    "public_world_event",
                    event.affected_agents[0] if event.affected_agents else None,
                    event.affected_agents[1] if len(event.affected_agents) > 1 else None,
                    payload=event.to_dict(),
                )
                store.set_meta(
                    meta_key,
                    json.dumps(
                        {"event_id": event_id, "payload": payload_json},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            return event_id

    @staticmethod
    def _ensure_scheduled_public_event(store: WorldStore) -> None:
        """Commit only the bounded, deterministic public-event catalogue.

        The catalogue is deliberately code-owned: model output can discuss an
        event but cannot invent or persist one. A per-day receipt makes retries
        and repeated scene opens idempotent.
        """

        day = int(store.get_meta("current_story_day", "1") or 1)
        events = _SCHEDULED_PUBLIC_EVENTS.get(day, ())
        if not events:
            return
        for event in events:
            receipt_key = f"scheduled_public_event:{event.event_key}"
            if store.get_meta(receipt_key) is not None:
                continue
            payload = event.to_dict()
            with store.transaction():
                event_id = store.append_event(
                    store.current_tick,
                    "public_world_event",
                    event.affected_agents[0] if event.affected_agents else None,
                    event.affected_agents[1] if len(event.affected_agents) > 1 else None,
                    payload=payload,
                )
                store.set_meta(
                    receipt_key,
                    json.dumps({"event_id": event_id, "event_key": event.event_key}, separators=(",", ":"), sort_keys=True),
                )

    @staticmethod
    def _ensure_character_story_events(store: WorldStore, day: int) -> None:
        """Materialize one stage of each canon arc, once per arc and day.

        The ledger is the durable hand-off between the simulation and the
        dialogue layer.  Storing the stage as an ordinary event keeps retries,
        graph generation, and later transcript inspection deterministic.
        """

        for arc, stage in character_story_arcs_for_day(day):
            receipt_key = f"character_story_stage:{arc.arc_id}:{stage.day}"
            if store.get_meta(receipt_key) is not None:
                continue
            payload = character_story_event(arc, stage)
            with store.transaction():
                if store.get_meta(receipt_key) is not None:
                    continue
                event_id = store.append_event(
                    store.current_tick,
                    "character_story_stage",
                    arc.owner_id,
                    arc.counterpart_id,
                    payload=payload,
                )
                store.set_meta(
                    receipt_key,
                    json.dumps(
                        {"event_id": event_id, "arc_id": arc.arc_id, "stage_day": stage.day},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )

    @staticmethod
    def _ensure_day_one_public_events(store: WorldStore, day: int) -> None:
        """Materialize the bounded Day 1 canon exactly once.

        The simulation bootstrap also emits internal story-arc events. Those
        are useful for continuity but are not customer-story source material;
        persist the three code-owned public headlines before selecting sources.
        """

        if day != 1:
            return
        for event in CODE_OWNED_DAY_ONE_EVENTS:
            receipt_key = f"day_one_public_event:{event.event_key}"
            if store.get_meta(receipt_key) is not None:
                continue
            with store.transaction():
                # Re-check inside the transaction so a retry cannot append a
                # duplicate receipt after an interrupted preparation.
                if store.get_meta(receipt_key) is not None:
                    continue
                event_id = store.append_event(
                    store.current_tick,
                    "public_world_event",
                    event.affected_agents[0] if event.affected_agents else None,
                    event.affected_agents[1] if len(event.affected_agents) > 1 else None,
                    payload=event.to_dict(),
                )
                store.set_meta(
                    receipt_key,
                    json.dumps(
                        {"event_id": event_id, "event_key": event.event_key},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )

    def tablet_feed(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raw_limit = request.get("limit", 5)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or not 1 <= raw_limit <= 8:
            raise ValueError("tablet feed limit must be between 1 and 8")
        with self._lock, WorldStore(self.db_path) as store:
            day = int(store.get_meta("current_story_day", "1") or 1)
            public_events = [
                event
                for event in store.list_events()
                if event["event_type"] == "public_world_event"
            ]
            items = []
            seen_keys: set[str] = set()
            seen_content: set[tuple[str, str]] = set()
            if day == 1 and not public_events:
                for index, event in enumerate(CODE_OWNED_DAY_ONE_EVENTS, start=1):
                    items.append(tablet_feed_item(1000000 + index, 0, event))
                    seen_keys.add(event.event_key)
                    seen_content.add((event.headline, event.summary))
                    if len(items) == raw_limit:
                        break
            for record in reversed(public_events):
                if len(items) >= raw_limit:
                    break
                payload = record["payload"]
                if not isinstance(payload, Mapping):
                    raise ValueError("persisted public world event was invalid")
                event = PublicWorldEvent.from_dict(payload)
                content_key = (event.headline, event.summary)
                if event.event_key in seen_keys or content_key in seen_content:
                    continue
                items.append(tablet_feed_item(record["event_id"], record["tick"], event))
                seen_keys.add(event.event_key)
                seen_content.add(content_key)
                if len(items) == raw_limit:
                    break
            return {
                "world_day": day,
                "items": items,
            }

    def prepare_story_day(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Prepare only deterministic day metadata; dialogue is on-demand."""

        with self._lock, WorldStore(self.db_path) as store:
            day_index = int(store.get_meta("current_story_day", "1") or 1)
            with store.transaction():
                migration = store.migrate_incompatible_daily_story(
                    day_index, DAILY_STORY_GRAPH_VERSION
                )
            if migration is not None:
                emit_timing("story_graph_migrated", **migration)
            self._release_legacy_save_gate(store)
            self._recover_unacknowledged_settlement(store)
            day_index = int(store.get_meta("current_story_day", "1") or 1)
            self._ensure_scheduled_public_event(store)
            shift_phase = store.get_meta("shift_phase", _SHIFT_PHASE_PLAYING)
            last_completed_story_day = int(
                store.get_meta("last_completed_story_day", "0") or 0
            )
            opening_seen = (
                store.get_meta(f"bridge_ack:opening_day_{day_index}") is not None
            )
        # The old implementation called the provider for every customer and
        # every possible drink branch here.  That made entering the room wait
        # for an entire day's dialogue.  A local skeleton is sufficient for
        # the cursor/order rules; the selected scene is materialized later by
        # the scene job worker.
        self.prepare_daily_story_skeleton(day_index)
        return {
            "world_day": day_index,
            "status": "ready",
            "opening_seen": opening_seen,
            "shift_phase": shift_phase,
            "last_completed_story_day": last_completed_story_day,
        }

    def _build_daily_story_skeleton(
        self,
        day_index: int,
        source_tick: int,
        events: tuple[dict[str, Any], ...],
        display_names: Mapping[str, str],
    ) -> DailyStoryGraph:
        """Build the cheap, provider-free cursor used before play begins."""

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
            customer = self._customer(self._participants(event))
            perspective = self._narrative_perspective(
                event, customer, display_names, source_tick
            )
            order = replace(
                order_for_customer(customer, int(event["event_id"])),
                order_id=f"order_day_{day_index}_{index}",
            )
            arrival_scene = replace(
                self._fallback_scene(
                    event,
                    display_names,
                    source_tick,
                    scene_id=f"{prefix}_order",
                    perspective=perspective,
                ),
                order=order,
            )
            branch_targets = tuple(
                (category.value, f"{prefix}_{category.value}")
                for category in ServiceCategory
            )
            nodes.append(
                StoryGraphNode(
                    arrival_id,
                    StoryNodeKind.ARRIVAL_ORDER,
                    order.customer_id,
                    perspective.event_topic,
                    scene=arrival_scene,
                    branch_targets=branch_targets,
                )
            )
            for category in ServiceCategory:
                result = self._candidate_result(order, category)
                reaction = self._fallback_reaction(
                    order,
                    result,
                    int(event["event_id"]),
                    scene_id=f"{prefix}_{category.value}",
                    event_topic=perspective.event_topic,
                    personal_stake=perspective.personal_stake,
                    unresolved_question=perspective.unresolved_question,
                )
                nodes.append(
                    StoryGraphNode(
                        f"{prefix}_{category.value}",
                        StoryNodeKind.RESULT_DIALOGUE,
                        order.customer_id,
                        self._result_premise(
                            order,
                            result,
                            perspective.event_topic,
                            perspective.personal_stake,
                            perspective.unresolved_question,
                        ),
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

    @staticmethod
    def _daily_interlude_scene(day_index: int, kind: str) -> ScenePackage:
        scenes: dict[str, tuple[SceneLine, ...]] = {
            # Runtime pre-opening scenes are event-driven via
            # _generated_pre_opening_scene.  This tiny neutral fixture keeps
            # the helper useful for callers that only need scene metadata.
            "pre_opening": (
                SceneLine("preopen_1", "dana", SPEAKER_PORTRAITS["dana"], "neutral", "今天的消息传得挺快。"),
                SceneLine("preopen_2", "jill", None, "neutral", "嗯，先听听客人怎么说。"),
            ),
            "music_selection": (
                SceneLine("music_1", "jill", None, "neutral", "好了……"),
            ),
            "break": (
                SceneLine("break_1", "jill", None, "neutral", "Boss，我要去休息一下了。"),
                SceneLine("break_2", "dana", SPEAKER_PORTRAITS["dana"], "neutral", "去吧。"),
            ),
        }
        return ScenePackage(f"{kind}_day_{day_index}", scenes[kind])

    @staticmethod
    def _event_anchor_terms(event_topic: str) -> tuple[str, ...]:
        """Return concrete two-character terms usable as dialogue anchors."""

        terms: list[str] = []
        current = ""
        def add_run(value: str) -> None:
            # Short n-grams let a natural paraphrase keep an anchor without
            # requiring the provider to repeat the whole premise verbatim.
            for size in (4, 3, 2):
                terms.extend(value[index : index + size] for index in range(len(value) - size + 1))

        for character in event_topic:
            if "\u4e00" <= character <= "\u9fff":
                current += character
                continue
            if len(current) >= 2:
                add_run(current)
            current = ""
        if len(current) >= 2:
            add_run(current)
        ignored = {
            "具体",
            "事情",
            "安排",
            "影响",
            "今晚",
            "正在",
            "来到",
            "酒吧",
            "城市",
            "顾客",
            "角色",
            "需要",
            "必须",
            "这件",
            "一个",
            "不能",
            "今晚",
            "个人",
            "具体",
        }
        return tuple(dict.fromkeys(term for term in terms if term not in ignored))

    @classmethod
    def _scene_mentions_event(cls, scene: ScenePackage, event_topic: str) -> bool:
        terms = cls._event_anchor_terms(event_topic)
        if not terms:
            return True
        text = "".join(line.text for line in scene.lines)
        return any(term in text for term in terms)

    @staticmethod
    def _short_event_topic(event_topic: str | None, limit: int = 36) -> str:
        """Keep one concrete event clause short enough for a dialogue line."""

        topic = " ".join(str(event_topic or "").split()).strip()
        if not topic:
            return "今晚的具体打算"
        topic = re.split(r"[。！？!?；;]", topic, maxsplit=1)[0].strip()
        if len(topic) > limit:
            topic = topic[:limit].rstrip("，,：: ")
        return topic or "今晚的具体打算"

    @staticmethod
    def _concise_event_clause(
        event: Mapping[str, Any], display_names: Mapping[str, str], event_topic: str
    ) -> str:
        """Turn a world event into one short spoken clause for local fallback."""

        details = event.get("payload")
        if isinstance(details, Mapping):
            headline = str(details.get("headline", "")).strip()
            if headline:
                return headline[:48]
        actor_id, target_id = WorldSceneService._participants(event)
        actor = display_names.get(actor_id, actor_id.title())
        target = display_names.get(target_id, target_id.title())
        clauses = {
            "worked": f"{actor}刚下班",
            "rested": f"{actor}刚休息过",
            "travelled": f"{actor}临时改了路线",
            "message_sent": f"{actor}发给{target}的消息",
            "talked": f"{actor}和{target}刚谈过的安排",
            "invitation_created": f"{actor}发给{target}的邀约",
            "invitation_kept": f"{actor}和{target}约好的见面",
            "invitation_declined": f"{actor}拒绝{target}的那次邀约",
            "promise_made": f"{actor}答应{target}的事",
            "promise_fulfilled": f"{actor}已经替{target}办妥的事",
            "promise_broken": f"{actor}没能替{target}办成的事",
        }
        return clauses.get(str(event.get("event_type", "")), event_topic[:48])

    def _generated_pre_opening_scene(
        self,
        day_index: int,
        event: Mapping[str, Any],
        display_names: Mapping[str, str],
        current_tick: int,
        engine: SimulationEngine,
        provider: ModelProvider,
    ) -> ScenePackage:
        """Generate the pre-opening exchange from the day's concrete event."""

        event_topic = self._event_premise(event, display_names, current_tick)
        event_clause = self._concise_event_clause(event, display_names, event_topic)
        scene_id = f"pre_opening_day_{day_index}"
        fallback = ScenePackage(
            scene_id,
            (
                SceneLine(
                    "preopen_1",
                    "dana",
                    SPEAKER_PORTRAITS["dana"],
                    "neutral",
                    f"今天外面都在谈{event_clause}。",
                ),
                SceneLine(
                    "preopen_2",
                    "jill",
                    None,
                    "neutral",
                    "总会有人来这里把后半段说完。",
                ),
            ),
        )
        generator = getattr(provider, "generate_dialogue_line", None)
        player_generator = getattr(provider, "generate_player_dialogue_line", None)
        if not callable(generator) or not callable(player_generator):
            return fallback
        premise = (
            f"开店前，Dana 和 Jill 谈起一个具体的城市近况：{event_topic}。"
            "不要谈检查、库存、灯光、开门步骤或点唱机。"
        )
        direction = self._scene_direction(
            "pre_opening",
            "从当天具体事件自然开口，短暂交换看法，不做开店说明",
            event_topic,
            shift_phase="pre_opening",
            event_topic=event_topic,
            personal_stake="这条消息会影响酒吧今晚听到的谈话",
            unresolved_question="客人会不会带来更确切的消息",
        )
        transcript: list[DialogueUtterance] = []
        try:
            dana_context = DialogueTurnContext(
                scene_id, 0, 4, premise,
                engine.context_for_agent(current_tick, "dana"),
                ("dana", "jill"), tuple(transcript), None, direction,
            )
            dana_proposed = generator(dana_context)
            dana = validate_dialogue_output(
                {"expression_id": dana_proposed.expression_id, "text": dana_proposed.text},
                dana_context,
            )
            transcript.append(DialogueUtterance("dana", dana.text))
            jill_context = PlayerDialogueTurnContext(
                scene_id, 1, 4, premise, ("dana", "jill"), tuple(transcript), None, direction,
            )
            jill_proposed = player_generator(jill_context)
            jill = validate_player_dialogue_output(
                {"expression_id": jill_proposed.expression_id, "text": jill_proposed.text},
                jill_context,
            )
            transcript.append(DialogueUtterance("jill", jill.text))
            dana_context = DialogueTurnContext(
                scene_id, 2, 4, premise,
                engine.context_for_agent(current_tick, "dana"),
                ("dana", "jill"), tuple(transcript), None, direction,
            )
            dana2_proposed = generator(dana_context)
            dana2 = validate_dialogue_output(
                {"expression_id": dana2_proposed.expression_id, "text": dana2_proposed.text},
                dana_context,
            )
            transcript.append(DialogueUtterance("dana", dana2.text))
            jill_context = PlayerDialogueTurnContext(
                scene_id, 3, 4, premise, ("dana", "jill"), tuple(transcript), None, direction,
            )
            jill2_proposed = player_generator(jill_context)
            jill2 = validate_player_dialogue_output(
                {"expression_id": jill2_proposed.expression_id, "text": jill2_proposed.text},
                jill_context,
            )
            generated = ScenePackage(
                scene_id,
                (
                    SceneLine("preopen_1", "dana", SPEAKER_PORTRAITS["dana"], dana.expression_id, dana.text),
                    SceneLine("preopen_2", "jill", None, "neutral", jill.text),
                    SceneLine("preopen_3", "dana", SPEAKER_PORTRAITS["dana"], dana2.expression_id, dana2.text),
                    SceneLine("preopen_4", "jill", None, "neutral", jill2.text),
                ),
            )
            if self._scene_mentions_event(generated, event_topic):
                return generated
        except Exception as exc:
            self._report_error("pre-opening dialogue generation", exc)
        return fallback

    def prepare_daily_story_skeleton(self, day_index: int) -> DailyStoryGraph:
        """Persist a provider-free day skeleton without making API calls."""

        if isinstance(day_index, bool) or not isinstance(day_index, int) or day_index < 1:
            raise ValueError("day_index must be a positive integer")
        with self._generation_lock, self._lock, WorldStore(self.db_path) as store:
            with store.transaction():
                migration = store.migrate_incompatible_daily_story(
                    day_index, DAILY_STORY_GRAPH_VERSION
                )
            if migration is not None:
                emit_timing("story_graph_migrated", **migration)
            record = store.get_daily_story_graph(day_index, DAILY_STORY_GRAPH_VERSION)
            if record is not None and record["status"] == "ready":
                raw_graph = record["graph"]
                if not isinstance(raw_graph, Mapping):
                    raise ValueError("ready daily story graph payload was invalid")
                return DailyStoryGraph.from_dict(raw_graph)
            self._engine(store, MockProvider())
            self._ensure_day_one_public_events(store, day_index)
            self._ensure_character_story_events(store, day_index)
            self._ensure_scheduled_public_event(store)
            if record is None:
                source_events = self._daily_source_events(store.list_events(), day_index)
                if not source_events:
                    raise ValueError("world did not contain a story source event")
                source_tick = store.current_tick
                source_event_ids = tuple(int(event["event_id"]) for event in source_events)
            else:
                source_tick = int(record["source_tick"])
                source_event_ids = tuple(record["source_event_ids"])
                source_events = tuple(
                    self._find_event(store, event_id) for event_id in source_event_ids
                )
            store.begin_daily_story_graph(
                day_index,
                DAILY_STORY_GRAPH_VERSION,
                source_tick,
                source_event_ids,
            )
            names = {agent.agent_id: agent.display_name for agent in store.list_agents()}
            graph = self._build_daily_story_skeleton(
                day_index, source_tick, source_events, names
            )
            store.complete_daily_story_graph(
                day_index, DAILY_STORY_GRAPH_VERSION, graph.to_dict()
            )
            emit_timing(
                "story_skeleton_ready",
                day=day_index,
                customer_count=len(source_events),
            )
            return graph

    def _recover_unacknowledged_settlement(self, store: WorldStore) -> None:
        """Commit a settlement whose final client acknowledgement was lost.

        The legacy room can transition home as soon as the final result text is
        dismissed, destroying the temporary bridge controller before the
        settlement request or its HTTP callback runs. A completed daily-story
        cursor is already authoritative, so a subsequent apartment
        preparation request is a safe recovery point.
        """

        day_index = int(store.get_meta("current_story_day", "1") or 1)
        progress = store.get_daily_story_progress(
            day_index, DAILY_STORY_GRAPH_VERSION
        )
        if progress is None or progress.get("status") != "completed":
            return
        income = int(store.get_meta("player_shift_income", "0") or 0)
        store.set_meta(f"player_shift_income_day_{day_index}", income)
        store.set_meta("player_shift_income", 0)
        store.set_meta("last_completed_story_day", day_index)
        store.set_meta("current_story_day", day_index + 1)
        store.set_meta("shift_phase", _SHIFT_PHASE_PLAYING)
        store.set_current_tick(store.current_tick + DAY_MINUTES)
        store.append_event(
            store.current_tick,
            "player_shift_completed",
            None,
            payload={
                "story_day": day_index,
                "income": income,
                "next_story_day": day_index + 1,
                "recovered": True,
            },
        )

    @staticmethod
    def _release_legacy_save_gate(store: WorldStore) -> None:
        """Make Stage 10 worlds playable without requiring a new paired save."""

        if (
            store.get_meta("shift_phase", _SHIFT_PHASE_PLAYING)
            == _SHIFT_PHASE_SAVE_REQUIRED
        ):
            store.set_meta("shift_phase", _SHIFT_PHASE_PLAYING)

    @staticmethod
    def _customer(participants: tuple[str, str]) -> str:
        if participants[0] == "dana":
            return participants[1]
        return participants[0]

    @staticmethod
    def _scene_direction(
        scene_type: str,
        beat: str,
        topic: str,
        *,
        relationship_tone: str = "熟人之间接话直接，保留各自的脾气和距离",
        unresolved_threads: tuple[str, ...] = (),
        avoid_patterns: tuple[str, ...] = (),
        shift_phase: str | None = None,
        music_policy: str | None = None,
        break_save: str | None = None,
        event_topic: str | None = None,
        personal_stake: str | None = None,
        unresolved_question: str | None = None,
    ) -> SceneDirection:
        from .lore import scene_direction_metadata, scene_direction_rules

        metadata = scene_direction_metadata(scene_type)
        if shift_phase is not None:
            phase_metadata = scene_direction_metadata(shift_phase)
            metadata = {
                "shift_phase": shift_phase,
                "music_policy": music_policy or phase_metadata["music_policy"],
                "break_save": break_save or phase_metadata["break_save"],
            }

        return SceneDirection(
            scene_type,
            beat,
            topic[:240],
            relationship_tone,
            unresolved_threads,
            avoid_patterns
            or (
                "欢迎光临",
                "请稍等",
                "我先找个位置坐",
                "吧台一直在这儿",
                "音乐不错",
                "今晚应该会更热闹",
            ),
            scene_direction_rules(scene_type),
            shift_phase or metadata["shift_phase"],
            music_policy or metadata["music_policy"],
            break_save or metadata["break_save"],
            event_topic or topic,
            personal_stake or relationship_tone,
            unresolved_question or (unresolved_threads[0] if unresolved_threads else topic),
        )

    @staticmethod
    def _fallback_scene(
        event: Mapping[str, Any],
        display_names: Mapping[str, str],
        current_tick: int,
        *,
        scene_id: str | None = None,
        event_topic: str | None = None,
        perspective: NarrativePerspective | None = None,
    ) -> ScenePackage:
        event_id = int(event["event_id"])
        participants = WorldSceneService._participants(event)
        customer = WorldSceneService._customer(participants)
        order = order_for_customer(customer, event_id)
        perspective = perspective or (
            WorldSceneService._perspective_for_event(event, display_names, current_tick)
            if event_topic is None
            else None
        )
        topic = (
            perspective.short_topic
            if perspective is not None
            else WorldSceneService._short_event_topic(event_topic)
        )
        event_opening = (
            perspective.event_topic
            if perspective is not None
            else (
                f"我过来时也听见有人提到{topic}。"
                if event_topic
                else "刚才路上听到点事，我想找个人聊聊。"
            )
        )
        event_opening = WorldSceneService._short_event_topic(event_opening, 72)
        arrival_topic = (
            perspective.short_topic
            if perspective is not None
            else topic
        )
        lines = (
            SceneLine(
                "fallback_1",
                customer,
                SPEAKER_PORTRAITS[customer],
                "neutral",
                event_opening,
            ),
            SceneLine(
                "fallback_2",
                "jill",
                None,
                "neutral",
                WorldSceneService._arrival_jill_line(customer, arrival_topic),
            ),
            SceneLine(
                "fallback_3",
                customer,
                SPEAKER_PORTRAITS[customer],
                "neutral",
                order.display_text,
            ),
        )
        return ScenePackage(scene_id or f"world_event_{event_id}", lines, order=order)

    @staticmethod
    def _arrival_jill_line(customer: str, topic: str) -> str:
        """Give Jill a character-specific first response to the customer's problem."""

        lines = {
            "alma": "路线又改了？先点杯喝的，别急着替明天发愁。",
            "sei": "人一多，原来的路就不保险了。先喝点什么？",
            "dorothy": "约见被积水拖住了？先来杯，别站着跟天气较劲。",
            "stella": "听起来不像一句抱怨就能解决。先喝点什么？",
            "dana": "又有安排卡在一起了？先坐下，把话说清楚。",
        }
        return lines.get(customer, f"{topic}先放一放。你想喝点什么？")

    def _generated_scene(
        self,
        event: Mapping[str, Any],
        display_names: Mapping[str, str],
        current_tick: int,
        engine: SimulationEngine,
        provider: ModelProvider,
        *,
        scene_id: str | None = None,
        shift_phase: str = "first_half",
        validation_fallback_reporter: Callable[[str], None] | None = None,
    ) -> ScenePackage:
        generator = getattr(provider, "generate_dialogue_line", None)
        player_generator = getattr(provider, "generate_player_dialogue_line", None)
        if not callable(generator) or not callable(player_generator):
            perspective = self._perspective_for_event(event, display_names, current_tick)
            return self._fallback_scene(
                event,
                display_names,
                current_tick,
                scene_id=scene_id,
                perspective=perspective,
            )

        event_id = int(event["event_id"])
        scene_id = scene_id or f"world_event_{event_id}"
        participants = self._participants(event)
        customer = self._customer(participants)
        order = order_for_customer(customer, event_id)
        public_participants = tuple(dict.fromkeys((*participants, "jill")))
        # In the original scripts the order is a pause inside a longer
        # conversation. Keep the generated scene to six short beats so the
        # event is established before the fixed order line appears.
        turn_count = 6
        perspective = self._narrative_perspective(
            event, customer, display_names, current_tick
        )
        event_topic = perspective.event_topic
        personal_stake = perspective.personal_stake
        unresolved_question = perspective.unresolved_question
        premise = (
            f"营业中的事件：{event_topic} "
            f"个人利害：{personal_stake}。"
        )
        unresolved_threads = (unresolved_question, "顾客还没有说出下一步打算")
        direction_kwargs = {
            "unresolved_threads": unresolved_threads,
            "shift_phase": shift_phase,
            "event_topic": event_topic,
            "personal_stake": personal_stake,
            "unresolved_question": unresolved_question,
        }
        transcript: list[DialogueUtterance] = []

        def customer_line(turn_index: int, beat: str) -> DialogueUtterance:
            context = DialogueTurnContext(
                scene_id,
                turn_index,
                turn_count,
                premise,
                engine.context_for_agent(current_tick, customer),
                public_participants,
                tuple(transcript),
                None,
                self._scene_direction("arrival_order", beat, event_topic, **direction_kwargs),
            )
            proposed = generator(context)
            return validate_dialogue_output(
                {"expression_id": proposed.expression_id, "text": proposed.text}, context
            )

        def jill_line(turn_index: int, beat: str) -> DialogueUtterance:
            context = PlayerDialogueTurnContext(
                scene_id,
                turn_index,
                turn_count,
                premise,
                public_participants,
                tuple(transcript),
                None,
                self._scene_direction("arrival_order", beat, event_topic, **direction_kwargs),
            )
            proposed = player_generator(context)
            return validate_player_dialogue_output(
                {"expression_id": proposed.expression_id, "text": proposed.text}, context
            )

        first = customer_line(0, "从一个具体异常或近况开口，不先谈酒")
        transcript.append(DialogueUtterance(customer, first.text))
        second = jill_line(1, "Jill 抓住事件中的一个词追问，不急着给建议")
        transcript.append(DialogueUtterance("jill", second.text))
        third = customer_line(2, "透露事件对自己、关系或今晚选择的实际影响")
        transcript.append(DialogueUtterance(customer, third.text))
        fourth = jill_line(3, "Jill 用干涩但具体的观察把谈话接回吧台节奏")
        transcript.append(DialogueUtterance("jill", fourth.text))
        transcript.append(DialogueUtterance(customer, order.display_text))
        fifth = jill_line(5, "点单打断事件后，Jill 简短确认并留下稍后继续谈的钩子")
        lines = (
            SceneLine("dialogue_1", customer, SPEAKER_PORTRAITS[customer], first.expression_id, first.text),
            SceneLine("dialogue_2", "jill", None, "neutral", second.text),
            SceneLine("dialogue_3", customer, SPEAKER_PORTRAITS[customer], third.expression_id, third.text),
            SceneLine("dialogue_4", "jill", None, "neutral", fourth.text),
            SceneLine("dialogue_5", customer, SPEAKER_PORTRAITS[customer], "neutral", order.display_text),
            SceneLine("dialogue_6", "jill", None, "neutral", fifth.text),
        )
        generated = ScenePackage(scene_id, lines, order=order)
        # A fluent but generic scene is worse than a short local fallback:
        # the day's event must be audible in at least one displayed line.
        if not self._scene_mentions_event(generated, perspective.anchor):
            if validation_fallback_reporter is not None:
                validation_fallback_reporter("missing_event_anchor")
            return self._fallback_scene(
                event,
                display_names,
                current_tick,
                scene_id=scene_id,
                perspective=perspective,
            )
        return generated

    @staticmethod
    def _result_closing(result: ServiceResult) -> str:
        return {
            ServiceCategory.EXACT: "嗯，就是这个。",
            ServiceCategory.ACCEPTABLE: "不是原来那杯，不过这个也不错。",
            ServiceCategory.WRONG: "……Jill，这杯好像不太对。",
            ServiceCategory.SPECIAL: "这个分量很有诚意。",
        }[result.category]

    @staticmethod
    def _result_premise(
        order: DrinkOrder,
        result: ServiceResult,
        event_topic: str | None = None,
        personal_stake: str | None = None,
        unresolved_question: str | None = None,
    ) -> str:
        meanings = {
            ServiceCategory.EXACT: "Jill准确完成了点单",
            ServiceCategory.ACCEPTABLE: "Jill做的不是原点单，但符合顾客公开偏好",
            ServiceCategory.WRONG: "Jill端出的饮品没有满足点单",
            ServiceCategory.SPECIAL: "Jill准确完成点单并做成了加大杯",
        }
        topic = event_topic or "刚才谈到的那件具体事情"
        stake = personal_stake or "这件事对顾客今晚的选择有影响"
        question = unresolved_question or "这件事还没有结论"
        return (
            f"吧台上的{result.beverage_name}只是对话中的短暂停顿；{topic}。"
            f"{stake}。服务事实是：{meanings[result.category]}。"
            f"饮品反应要简短，回到尚未解决的问题：{question}。"
        )

    @staticmethod
    def _fallback_reaction(
        order: DrinkOrder,
        result: ServiceResult,
        service_event_id: int,
        *,
        scene_id: str | None = None,
        event_topic: str | None = None,
        personal_stake: str | None = None,
        unresolved_question: str | None = None,
    ) -> ScenePackage:
        # Fallbacks are user-visible dialogue too. Keep a concise topic and
        # an actual choice instead of repeating the complete event premise.
        topic = WorldSceneService._fallback_topic_label(
            order.customer_id, event_topic, unresolved_question
        )
        decision = WorldSceneService._fallback_decision_prompt(
            order.customer_id, topic, personal_stake, unresolved_question
        )
        opening = {
            ServiceCategory.EXACT: f"这杯{result.beverage_name}正合适。",
            ServiceCategory.ACCEPTABLE: f"{result.beverage_name}？和我点的不一样，不过可以试试。",
            ServiceCategory.WRONG: "这个味道不对。你是不是拿错杯子了？",
            ServiceCategory.SPECIAL: f"加大杯的{result.beverage_name}？今天这么大方？",
        }[result.category]
        jill_prefix = {
            ServiceCategory.EXACT: "那就好。",
            ServiceCategory.ACCEPTABLE: "先喝一口。",
            ServiceCategory.WRONG: "是我失手了，下一杯我重做。",
            ServiceCategory.SPECIAL: "杯子大了。",
        }[result.category]
        jill_line = f"{jill_prefix}{decision}"
        closing = WorldSceneService._fallback_departure(
            order.customer_id, topic, personal_stake
        )
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
                closing,
            ),
        )
        return ScenePackage(scene_id or f"order_result_{service_event_id}", lines)

    @staticmethod
    def _fallback_topic_label(
        customer: str, event_topic: str | None, unresolved_question: str | None
    ) -> str:
        """Reduce an event premise to the noun phrase people actually say."""

        source = f"{event_topic or ''} {unresolved_question or ''}"
        if customer == "alma":
            if "客户" in source or "路线" in source or "交通" in source:
                return "明早见客户的安排"
            return "明早的安排"
        if customer == "sei":
            if "接人" in source or "护送" in source or "人流" in source or "路线" in source:
                return "明晚那条接人路线"
            return "明晚的安排"
        if customer == "dorothy":
            if "积水" in source or "约见" in source or "见面" in source:
                return "那场被积水耽误的约见"
            return "那场约见"
        if customer == "stella":
            return "刚才那件事"
        if customer == "dana":
            return "今晚的安排"
        return WorldSceneService._short_event_topic(event_topic or unresolved_question)

    @staticmethod
    def _fallback_decision_prompt(
        customer: str,
        topic: str,
        personal_stake: str | None,
        unresolved_question: str | None,
    ) -> str:
        """Phrase the unresolved choice without echoing the full premise."""

        source = f"{personal_stake or ''} {unresolved_question or ''}"
        if customer == "alma":
            if "客户" in source or "路线" in source or "交通" in source:
                return f"{topic}，你准备绕过去，还是把见面改天？"
            return f"{topic}，你打算怎么安排？"
        if customer == "sei":
            if "路线" in source or "护送" in source or "人流" in source:
                return f"{topic}要换路，还是早点出发？"
            return f"{topic}，你准备怎么处理？"
        if customer == "dorothy":
            if "约见" in source or "见面" in source or "时间" in source:
                return f"{topic}还照原来的时间吗？"
            return f"{topic}，你想好下一步了吗？"
        return f"{topic}，你准备怎么处理？"

    @staticmethod
    def _fallback_departure(
        customer: str, topic: str, personal_stake: str | None
    ) -> str:
        """End the beat with an explicit, character-neutral departure."""

        if customer == "alma":
            return "我先走了，明早的安排我再算一遍。"
        if customer == "sei":
            return "我先走了，今晚把路线重新排一下。"
        if customer == "dorothy":
            return "我先走了，约见的时间我再确认一下。"
        if customer == "stella":
            return "我先走了，这件事我回去再想想。"
        return "我先走了，今晚的安排我回去再想想。"

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
        shift_phase: str = "first_half",
        event_topic: str | None = None,
        personal_stake: str | None = None,
        unresolved_question: str | None = None,
    ) -> ScenePackage:
        generator = getattr(provider, "generate_dialogue_line", None)
        player_generator = getattr(provider, "generate_player_dialogue_line", None)
        if not callable(generator) or not callable(player_generator):
            return self._fallback_reaction(
                order,
                result,
                service_event_id,
                scene_id=scene_id,
                event_topic=event_topic,
                personal_stake=personal_stake,
                unresolved_question=unresolved_question,
            )
        scene_id = scene_id or f"order_result_{service_event_id}"
        participants = (order.customer_id, "jill")
        premise = self._result_premise(
            order,
            result,
            event_topic,
            personal_stake,
            unresolved_question,
        )
        unresolved_threads = (
            unresolved_question or "刚才谈到的事情还没有结论",
            personal_stake or "这件事对顾客今晚的选择仍有影响",
        )
        customer_context = DialogueTurnContext(
            scene_id,
            0,
            3,
            premise,
            engine.context_for_agent(current_tick, order.customer_id),
            participants,
            (),
            result,
            self._scene_direction(
                "service_reaction",
                "短暂确认出杯事实，然后回到人物正在处理的事件",
                premise,
                unresolved_threads=unresolved_threads,
                shift_phase=shift_phase,
                event_topic=event_topic,
                personal_stake=personal_stake,
                unresolved_question=unresolved_question,
            ),
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
            self._scene_direction(
                "service_reaction",
                "Jill 用一句短话承认服务结果，再接回顾客刚才提到的事件细节",
                premise,
                unresolved_threads=unresolved_threads,
                shift_phase=shift_phase,
                event_topic=event_topic,
                personal_stake=personal_stake,
                unresolved_question=unresolved_question,
            ),
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
        closing_context = DialogueTurnContext(
            scene_id,
            2,
            3,
            premise,
            engine.context_for_agent(current_tick, order.customer_id),
            participants,
            tuple(transcript),
            result,
            self._scene_direction(
                "service_reaction",
                "回到事件的未解决问题，留下继续营业时可以再谈的具体钩子",
                premise,
                unresolved_threads=unresolved_threads,
                shift_phase=shift_phase,
                event_topic=event_topic,
                personal_stake=personal_stake,
                unresolved_question=unresolved_question,
            ),
        )
        closing_proposed = generator(closing_context)
        closing_draft = validate_dialogue_output(
            {
                "expression_id": closing_proposed.expression_id,
                "text": closing_proposed.text,
            },
            closing_context,
        )
        generated = ScenePackage(
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
                    closing_draft.expression_id,
                    closing_draft.text,
                ),
                SceneLine(
                    "dialogue_4",
                    order.customer_id,
                    SPEAKER_PORTRAITS[order.customer_id],
                    "neutral",
                    "那我先走了，回头再聊。",
                ),
            ),
        )
        if event_topic and not self._scene_mentions_event(generated, event_topic):
            return self._fallback_reaction(
                order,
                result,
                service_event_id,
                scene_id=scene_id,
                event_topic=event_topic,
                personal_stake=personal_stake,
                unresolved_question=unresolved_question,
            )
        return generated

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
        story_day: int | None = None,
    ) -> tuple[dict[str, Any], ...]:
        selected: list[dict[str, Any]] = []
        customers: set[str] = set()
        # Character arcs are the concrete canon for bar conversations. Public
        # headlines remain useful background and only fill an empty seat.
        character_events = [
            event
            for event in events
            if event["event_type"] == "character_story_stage"
            and (
                story_day is None
                or not isinstance(event.get("payload"), Mapping)
                or int(event["payload"].get("stage_day", -1)) == story_day
            )
        ]
        has_character_canon = bool(character_events)
        if has_character_canon and story_day is not None:
            # Three customers fit in one vanilla shift. Rotate the five seed
            # characters across days so every arc can reach the bar while the
            # order remains deterministic and replayable.
            by_customer: dict[str, dict[str, Any]] = {}
            for event in character_events:
                customer = WorldSceneService._customer(
                    WorldSceneService._participants(event)
                )
                by_customer.setdefault(customer, event)
            rotation = ("alma", "sei", "dorothy", "stella", "dana")
            start = ((story_day - 1) * 2) % len(rotation)
            character_events = [
                by_customer[customer]
                for offset in range(len(rotation))
                if (customer := rotation[(start + offset) % len(rotation)])
                in by_customer
            ]
        ordered = [
            *character_events,
            *reversed(events),
        ] if has_character_canon else [
            *[event for event in events if event["event_type"] == "public_world_event"],
            *reversed(events),
        ]
        for event in ordered:
            if event["event_type"] not in _NARRATIVE_EVENT_TYPES:
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
        if not has_character_canon:
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
            # The vanilla day-10+ rhythm places the break before the third
            # customer. Keep that boundary in provider context as well, so
            # second-half lines do not accidentally reopen the day or talk as
            # if the jukebox had just been selected. The native jukebox is
            # reopened separately by the GameMaker break hand-off.
            shift_phase = "first_half" if index < 3 else "second_half"
            prefix = f"day_{day_index}_customer_{index}"
            arrival_id = f"{prefix}_arrival"
            merge_id = f"{prefix}_merge"
            next_arrival_id = (
                f"day_{day_index}_customer_{index + 1}_arrival"
                if index < len(events)
                else None
            )
            perspective = self._narrative_perspective(
                event,
                self._customer(self._participants(event)),
                display_names,
                source_tick,
            )
            topic = perspective.event_topic
            arrival_scene = self._generated_scene(
                event,
                display_names,
                source_tick,
                engine,
                provider,
                scene_id=f"{prefix}_order",
                shift_phase=shift_phase,
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
                result_topic = self._result_premise(
                    order,
                    result,
                    topic,
                    perspective.personal_stake,
                    perspective.unresolved_question,
                )
                reaction = self._generated_reaction(
                    order,
                    result,
                    int(event["event_id"]),
                    source_tick,
                    engine,
                    provider,
                    scene_id=f"{prefix}_{category.value}",
                    shift_phase=shift_phase,
                    event_topic=topic,
                    personal_stake=perspective.personal_stake,
                    unresolved_question=perspective.unresolved_question,
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
        started = monotonic_seconds()
        emit_timing("story_graph_start", day=day_index)
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
                emit_timing(
                    "story_graph_ready_cached",
                    day=day_index,
                    elapsed_ms=round((monotonic_seconds() - started) * 1000),
                )
                return graph

            if record is None:
                self._engine(store, MockProvider())
                self._ensure_day_one_public_events(store, day_index)
                self._ensure_character_story_events(store, day_index)
                source_events = self._daily_source_events(store.list_events(), day_index)
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
                try:
                    graph = self._build_daily_story_graph(
                        day_index,
                        source_tick,
                        source_events,
                        display_names,
                        engine,
                        provider,
                    )
                except BYOKError as exc:
                    if not self.allow_provider_fallback:
                        raise
                    # A remote provider is useful for richer drafts, but a
                    # transient transport/response/budget error must not make
                    # the player's first O.S. day unplayable. Regenerate the
                    # bounded graph with deterministic local dialogue; no
                    # provider text or failed draft is committed.
                    self._report_error("daily story provider fallback", exc)
                    emit_timing(
                        "story_graph_provider_fallback",
                        day=day_index,
                        error_type=type(exc).__name__,
                    )
                    fallback_provider = MockProvider()
                    graph = self._build_daily_story_graph(
                        day_index,
                        source_tick,
                        source_events,
                        display_names,
                        self._engine(store, fallback_provider),
                        fallback_provider,
                    )
                store.complete_daily_story_graph(
                    day_index,
                    DAILY_STORY_GRAPH_VERSION,
                    graph.to_dict(),
                )
                emit_timing(
                    "story_graph_ready",
                    day=day_index,
                    customer_count=len(source_events),
                    elapsed_ms=round((monotonic_seconds() - started) * 1000),
                )
                return graph
            except Exception as exc:
                emit_timing(
                    "story_graph_error",
                    day=day_index,
                    elapsed_ms=round((monotonic_seconds() - started) * 1000),
                    error_type=type(exc).__name__,
                )
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
        if not scene.lines:
            return
        # Arrival and result scenes may use local fallback line ids.  They
        # are still real customer conversations and should feed later
        # continuity; ambient/interlude scenes must remain out of memory.
        is_customer_scene = scene.order is not None or "_customer_" in scene.scene_id
        if not is_customer_scene and not all(
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
                "Jill 到了酒吧。",
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
                    "需要时可以在平板中存档；准备好后就能开始新的营业日。",
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
        # Local-only and fast. Run before taking the scene lock so an older
        # database cannot invert the scene/generation lock order.
        self.prepare_daily_story_skeleton(day_index)
        request_id = str(request["request_id"])
        request_json = json.dumps(
            dict(request), separators=(",", ":"), sort_keys=True
        )
        with self._lock, WorldStore(self.db_path) as store:
            self._release_legacy_save_gate(store)
            self._ensure_scheduled_public_event(store)
            graph_record = store.get_daily_story_graph(
                day_index, DAILY_STORY_GRAPH_VERSION
            )
            if graph_record is None or graph_record["status"] != "ready":
                raise BridgeError(
                    503,
                    "story_skeleton_unavailable",
                    "the local story skeleton could not be prepared",
                )
            raw_graph = graph_record["graph"]
            if not isinstance(raw_graph, Mapping):
                raise ValueError("ready daily story graph payload was invalid")
            graph = DailyStoryGraph.from_dict(raw_graph)
            # Clear stale client hand-off markers before any opening gate can
            # return a scene. Older acceptance databases may retain this flag
            # even though the cursor is not at customer three anymore.
            break_pending_key = f"break_pending_day_{day_index}"
            break_pending = store.get_meta(break_pending_key) == "1"
            progress = store.get_daily_story_progress(
                day_index, DAILY_STORY_GRAPH_VERSION
            )
            expected_break_node = f"day_{day_index}_customer_3_arrival"
            if break_pending and (
                progress is None
                or progress["status"] != "active"
                or progress["current_node_id"] != expected_break_node
            ):
                store.set_meta(break_pending_key, "0")
                emit_timing(
                    "daily_flow_gate",
                    day=day_index,
                    gate="break_stale_cleared",
                    current_node_id=(
                        progress["current_node_id"] if progress is not None else None
                    ),
                )
                break_pending = False
            opening = self._ambient_scene(day_index, "opening")
            if store.get_meta(f"bridge_ack:{opening.scene_id}") is None:
                return self._persist_ambient_request(store, request, opening)
            doorbell = self._ambient_scene(day_index, "doorbell")
            if store.get_meta(f"bridge_ack:{doorbell.scene_id}") is None:
                return self._persist_ambient_request(store, request, doorbell)
            pre_opening_key = f"story_materialized_scene:pre_opening_day_{day_index}"
            pre_opening_payload = store.get_meta(pre_opening_key)
            if isinstance(pre_opening_payload, str):
                try:
                    pre_opening = ScenePackage.from_dict(json.loads(pre_opening_payload))
                except (TypeError, ValueError, json.JSONDecodeError):
                    pre_opening = None
            else:
                pre_opening = None
            if pre_opening is None:
                source_events = self._daily_source_events(store.list_events(), day_index)
                if not source_events:
                    raise ValueError("world did not contain a story source event")
                names = {
                    agent.agent_id: agent.display_name for agent in store.list_agents()
                }
                try:
                    provider = self.provider_factory()
                    pre_opening = self._generated_pre_opening_scene(
                        day_index,
                        source_events[0],
                        names,
                        store.current_tick,
                        self._engine(store, provider),
                        provider,
                    )
                except Exception as exc:
                    if not self.allow_provider_fallback:
                        raise
                    self._report_error("pre-opening dialogue provider fallback", exc)
                    pre_opening = self._generated_pre_opening_scene(
                        day_index,
                        source_events[0],
                        names,
                        store.current_tick,
                        self._engine(store, MockProvider()),
                        MockProvider(),
                    )
                store.set_meta(
                    pre_opening_key,
                    json.dumps(pre_opening.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                )
            if store.get_meta(f"bridge_ack:{pre_opening.scene_id}") is None:
                emit_timing("daily_flow_gate", day=day_index, gate="pre_opening")
                return self._persist_ambient_request(store, request, pre_opening)
            music_selection = self._daily_interlude_scene(day_index, "music_selection")
            if store.get_meta(f"bridge_ack:{music_selection.scene_id}") is None:
                emit_timing("daily_flow_gate", day=day_index, gate="music_selection")
                return self._persist_ambient_request(store, request, music_selection)
            break_scene = self._daily_interlude_scene(day_index, "break")
            if break_pending:
                if store.get_meta(f"bridge_ack:{break_scene.scene_id}") is None:
                    emit_timing("daily_flow_gate", day=day_index, gate="break")
                    return self._persist_ambient_request(store, request, break_scene)
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
                StoryNodeKind.INTERLUDE,
                StoryNodeKind.ARRIVAL_ORDER,
                StoryNodeKind.RESULT_DIALOGUE,
            } or node.scene is None:
                raise ValueError("daily story cursor did not reference a playable scene")
            if node.kind in {StoryNodeKind.ARRIVAL_ORDER, StoryNodeKind.RESULT_DIALOGUE}:
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
            else:
                source_event_id = 0
            scene = node.scene
            materialized_key = f"story_materialized_scene:{scene.scene_id}"
            materialized_payload = store.get_meta(materialized_key)
            if materialized_payload is not None:
                persisted_materialized = json.loads(materialized_payload)
                if isinstance(persisted_materialized, dict):
                    scene = ScenePackage.from_dict(persisted_materialized)
            elif node.kind is StoryNodeKind.ARRIVAL_ORDER:
                # The skeleton contains the authoritative order and node IDs;
                # only the selected arrival dialogue is sent to the provider.
                event = self._find_event(store, source_event_id)
                names = {
                    agent.agent_id: agent.display_name
                    for agent in store.list_agents()
                }
                try:
                    provider = self.provider_factory()
                    generated = self._generated_scene(
                        event,
                        names,
                        store.current_tick,
                        self._engine(store, provider),
                        provider,
                        scene_id=scene.scene_id,
                        validation_fallback_reporter=lambda reason: self._record_scene_validation_fallback(
                            store,
                            scene_id=scene.scene_id,
                            source_event_id=source_event_id,
                            story_day=day_index,
                            reason=reason,
                        ),
                    )
                except Exception as exc:
                    if not self.allow_provider_fallback:
                        raise
                    self._report_error("arrival dialogue provider fallback", exc)
                    store.append_event(
                        store.current_tick,
                        "dialogue_provider_fallback",
                        event.get("actor_id"),
                        event.get("target_id"),
                        {
                            "error_type": type(exc).__name__,
                            "source_event_id": source_event_id,
                            "story_day": day_index,
                            "scene_id": scene.scene_id,
                        },
                    )
                    generated = self._fallback_scene(
                        event,
                        names,
                        store.current_tick,
                        scene_id=scene.scene_id,
                        perspective=self._perspective_for_event(event, names, store.current_tick),
                    )
                if generated.order is None or node.scene.order is None:
                    raise ValueError("generated arrival did not contain an order")
                scene = replace(generated, order=replace(generated.order, order_id=node.scene.order.order_id))
                store.set_meta(
                    materialized_key,
                    json.dumps(scene.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                )
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
                store.set_meta(
                    f"bridge_scene:{scene.scene_id}",
                    "ambient" if node.kind is StoryNodeKind.INTERLUDE else source_event_id,
                )
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

    def scene_speaker_hint(self, request: Mapping[str, Any]) -> str | None:
        """Return the next known portrait without constructing a provider."""

        if not self.daily_story_mode:
            return None
        with self._lock, WorldStore(self.db_path) as store:
            day_index = int(store.get_meta("current_story_day", "1") or 1)
            if store.get_meta(f"bridge_ack:opening_day_{day_index}") is None:
                return None
            graph_record = store.get_daily_story_graph(
                day_index, DAILY_STORY_GRAPH_VERSION
            )
            if graph_record is None or graph_record["status"] != "ready":
                return None
            if store.get_meta(f"bridge_ack:doorbell_day_{day_index}") is None:
                return None
            raw_graph = graph_record["graph"]
            if not isinstance(raw_graph, Mapping):
                return None
            graph = DailyStoryGraph.from_dict(raw_graph)
            progress = store.get_daily_story_progress(
                day_index, DAILY_STORY_GRAPH_VERSION
            )
            if progress is None or progress["status"] == "completed":
                return None
            node = self._story_node(graph, str(progress["current_node_id"]))
            return node.customer_id

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
                    self._ensure_scheduled_public_event(store)
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
        """Commit the deterministic drink result before optional dialogue AI."""

        order = source_scene.order
        if order is None:
            raise KeyError("story scene did not contain an order")
        day_index = int(story_reference["day_index"])
        arrival_node_id = str(story_reference["node_id"])
        graph_record = store.get_daily_story_graph(day_index, DAILY_STORY_GRAPH_VERSION)
        if graph_record is None or not isinstance(graph_record["graph"], Mapping):
            raise ValueError("daily story graph was unavailable during order resolution")
        graph = DailyStoryGraph.from_dict(graph_record["graph"])
        arrival = self._story_node(graph, arrival_node_id)
        if arrival.kind is not StoryNodeKind.ARRIVAL_ORDER:
            raise ValueError("story order did not reference an arrival node")
        progress = store.get_daily_story_progress(day_index, DAILY_STORY_GRAPH_VERSION)
        existing_commit = store.get_story_branch_commit(order.order_id)
        if progress is None or (progress["current_node_id"] != arrival_node_id and existing_commit is None):
            raise BridgeError(409, "story_branch_already_advanced", "the daily story had already advanced past this order")

        request_key = f"bridge_order_request:{request['request_id']}"
        order_key = f"bridge_order:{order.order_id}"
        local_started = monotonic_seconds()
        result: ServiceResult
        service_event_id: int
        income_delta: int
        fallback_scene: ScenePackage
        result_node_id: str
        replay = False
        with store.transaction():
            prior_request = store.get_meta(request_key)
            if prior_request is not None and prior_request != request_json:
                raise BridgeError(409, "request_id_conflict", "request_id was already used with different content")
            prior_order = store.get_meta(order_key)
            if prior_order is not None:
                replay = True
                record = json.loads(prior_order)
                if record.get("resolution_input") != resolution_input:
                    raise BridgeError(409, "order_already_resolved", "the order was already resolved with a different drink")
                result = ServiceResult.from_dict(record["result"])
                service_event_id = int(record["service_event_id"])
                income_delta = int(record["income_delta"])
                fallback_scene = ScenePackage.from_dict(record["scene"])
                result_node_id = dict(arrival.branch_targets)[result.category.value]
                store.set_meta(request_key, request_json)
            else:
                result = evaluate_service(order, submission)
                result_node_id = dict(arrival.branch_targets)[result.category.value]
                result_node = self._story_node(graph, result_node_id)
                if result_node.scene is None:
                    raise ValueError("selected daily result branch had no scene")
                fallback_scene = result_node.scene
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
                store.advance_daily_story_cursor(day_index, DAILY_STORY_GRAPH_VERSION, arrival_node_id, result_node_id)
                total_income = int(store.get_meta("player_shift_income", "0") or 0)
                store.set_meta("player_shift_income", total_income + income_delta)
                record = {
                    "resolution_input": resolution_input,
                    "service_event_id": service_event_id,
                    "result": result.to_dict(),
                    "scene": fallback_scene.to_dict(),
                    "income_delta": income_delta,
                }
                store.set_meta(order_key, json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
                store.set_meta(request_key, request_json)
                store.set_meta(f"bridge_scene:{fallback_scene.scene_id}", service_event_id)
                store.set_meta(f"bridge_scene_payload:{fallback_scene.scene_id}", json.dumps(fallback_scene.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True))
                store.set_meta(f"story_scene_node:{fallback_scene.scene_id}", json.dumps({"day_index": day_index, "node_id": result_node_id}, separators=(",", ":")))

        emit_timing(
            "order_local_committed",
            order_id=order.order_id,
            result_category=result.category.value,
            service_event_id=service_event_id,
            income_delta=income_delta,
            replay=replay,
            elapsed_ms=round((monotonic_seconds() - local_started) * 1000),
        )
        if replay:
            return OrderResolution(result, fallback_scene, income_delta)
        reaction_scene = fallback_scene
        provider_started = monotonic_seconds()
        provider_error: Exception | None = None
        event_topic = arrival.topic
        event_reference = self._short_event_topic(event_topic)
        try:
            provider = self.provider_factory()
            reaction_scene = self._generated_reaction(
                order, result, service_event_id, store.current_tick,
                self._engine(store, provider), provider, scene_id=fallback_scene.scene_id,
                event_topic=event_topic,
                personal_stake=f"{event_reference}仍影响{order.customer_id}今晚的选择",
                unresolved_question=f"{event_reference}接下来会怎样",
            )
        except Exception as exc:
            provider_error = exc
            reaction_scene = fallback_scene
            self._report_error("selected result provider fallback", exc)

        if provider_error is not None:
            with store.transaction():
                store.append_event(
                    store.current_tick,
                    "dialogue_provider_fallback",
                    order.customer_id,
                    payload={
                        "error_type": type(provider_error).__name__,
                        "source_event_id": service_event_id,
                        "order_id": order.order_id,
                        "result_category": result.category.value,
                    },
                )
            emit_timing("order_reaction_fallback", order_id=order.order_id, service_event_id=service_event_id, result_category=result.category.value, error_type=type(provider_error).__name__, elapsed_ms=round((monotonic_seconds() - provider_started) * 1000))
        else:
            with store.transaction():
                latest = json.loads(store.get_meta(order_key) or "{}")
                if latest.get("resolution_input") != resolution_input:
                    raise BridgeError(409, "order_already_resolved", "the order resolution changed while generating dialogue")
                latest["scene"] = reaction_scene.to_dict()
                store.set_meta(order_key, json.dumps(latest, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
                store.set_meta(f"bridge_scene:{reaction_scene.scene_id}", service_event_id)
                store.set_meta(f"bridge_scene_payload:{reaction_scene.scene_id}", json.dumps(reaction_scene.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True))
                store.set_meta(f"story_scene_node:{reaction_scene.scene_id}", json.dumps({"day_index": day_index, "node_id": result_node_id}, separators=(",", ":")))
            emit_timing("order_reaction_ready", order_id=order.order_id, service_event_id=service_event_id, result_category=result.category.value, elapsed_ms=round((monotonic_seconds() - provider_started) * 1000))
        return OrderResolution(result, reaction_scene, income_delta)

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
            replay = False
            replay_scene: ScenePackage | None = None
            local_started = monotonic_seconds()
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
                        replay = True
                        replay_scene = ScenePackage.from_dict(persisted_scene)
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

            emit_timing(
                "order_local_committed",
                order_id=order.order_id,
                result_category=result.category.value,
                service_event_id=service_event_id,
                income_delta=0,
                replay=replay,
                elapsed_ms=round((monotonic_seconds() - local_started) * 1000),
            )
            if replay_scene is not None:
                return OrderResolution(result, replay_scene)

            provider_started = monotonic_seconds()
            provider_error: Exception | None = None
            try:
                provider = self.provider_factory()
                engine = self._engine(store, provider)
                reaction = self._generated_reaction(
                    order,
                    result,
                    service_event_id,
                    store.current_tick,
                    engine,
                    provider,
                )
            except Exception as exc:
                provider_error = exc
                self._report_error("drink reaction generation", exc)
                store.append_event(
                    store.current_tick,
                    "dialogue_provider_error",
                    order.customer_id,
                    payload={
                        "error_type": type(exc).__name__,
                        "source_event_id": service_event_id,
                        "order_id": order.order_id,
                        "result_category": result.category.value,
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
            emit_timing(
                "order_reaction_fallback" if provider_error is not None else "order_reaction_ready",
                order_id=order.order_id,
                result_category=result.category.value,
                service_event_id=service_event_id,
                error_type=type(provider_error).__name__ if provider_error is not None else None,
                elapsed_ms=round((monotonic_seconds() - provider_started) * 1000),
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
                        next_node_id = merge.next_node_id or ""
                        if next_node_id.endswith("customer_3_arrival"):
                            store.set_meta(
                                f"break_pending_day_{day_index}",
                                "1",
                            )
                    elif node.kind is StoryNodeKind.INTERLUDE:
                        store.advance_daily_story_cursor(
                            day_index,
                            DAILY_STORY_GRAPH_VERSION,
                            node_id,
                            node.next_node_id,
                        )
                if scene_id.startswith("settlement_day_"):
                    try:
                        completed_day = int(scene_id.removeprefix("settlement_day_"))
                    except ValueError:
                        raise ValueError("settlement scene day was invalid") from None
                    current_day = int(store.get_meta("current_story_day", "1") or 1)
                    if completed_day != current_day:
                        # A room transition can destroy the GameMaker bridge
                        # immediately after the settlement text.  On the next
                        # prepare request the completed cursor is recovered
                        # and current_story_day is advanced before this stale
                        # acknowledgement arrives.  Treat that exact
                        # already-completed settlement as idempotent success;
                        # unrelated day mismatches remain hard failures.
                        recovered_day = int(
                            store.get_meta("last_completed_story_day", "0") or 0
                        )
                        if (
                            completed_day + 1 == current_day
                            and recovered_day == completed_day
                        ):
                            ack_event_id = store.append_event(
                                store.current_tick,
                                "player_scene_ack",
                                None,
                                payload={
                                    "scene_id": scene_id,
                                    "client_session_id": request["client_session_id"],
                                    "outcome": request["outcome"],
                                    "recovered": True,
                                },
                            )
                            store.set_meta(ack_key, ack_event_id)
                            store.set_meta(request_key, request_json)
                            return
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
                    store.set_meta("shift_phase", _SHIFT_PHASE_PLAYING)
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
                if scene_id.startswith("break_day_"):
                    try:
                        break_day = int(scene_id.removeprefix("break_day_"))
                    except ValueError:
                        raise ValueError("break scene day was invalid") from None
                    store.set_meta(f"break_pending_day_{break_day}", "0")
                if scene_id.startswith("music_selection_day_"):
                    try:
                        music_day = int(scene_id.removeprefix("music_selection_day_"))
                    except ValueError:
                        raise ValueError("music selection day was invalid") from None
                    store.set_meta(f"music_selected_day_{music_day}", "1")
                if not ambient:
                    self._remember_generated_dialogue(store, scene, event_id)
                transcript_lines = [
                    {
                        "line_id": line.line_id,
                        "speaker_id": line.speaker_id or "",
                        "text": line.text,
                    }
                    for line in scene.lines
                ]
                story_day = self._story_day_for_scene(scene_id)
                store.append_event(
                    store.current_tick,
                    "dialogue_transcript",
                    None,
                    payload={
                        "story_day": story_day,
                        "scene_id": scene_id,
                        "lines": transcript_lines,
                    },
                )
                emit_dialogue_transcript(story_day, scene_id, transcript_lines)
                ack_event_id = store.append_event(
                    store.current_tick,
                    "player_scene_ack",
                    None,
                    payload={
                        "scene_id": scene_id,
                        "client_session_id": request["client_session_id"],
                        "outcome": request["outcome"],
                        **(
                            {"music_source": str(request["music_source"])}
                            if scene_id.startswith("music_selection_day_")
                            and request.get("music_source") is not None
                            else {}
                        ),
                    },
                )
                store.set_meta(ack_key, ack_event_id)
                store.set_meta(request_key, request_json)

    def complete_paired_save(
        self, world_day: int, last_completed_story_day: int
    ) -> None:
        """Accept Stage 10 save callbacks without making saving a day gate."""

        with self._lock, WorldStore(self.db_path) as store, store.transaction():
            current_day = int(store.get_meta("current_story_day", "1") or 1)
            completed_day = int(store.get_meta("last_completed_story_day", "0") or 0)
            if current_day == world_day and completed_day == last_completed_story_day:
                self._release_legacy_save_gate(store)
