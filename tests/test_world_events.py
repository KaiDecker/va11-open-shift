from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from open_shift.bridge import BridgeApplication, BridgeConfig, BridgeError
from open_shift.providers import MockProvider
from open_shift.world_bridge import (
    SCHEDULED_PUBLIC_EVENT_VERSION,
    _SCHEDULED_PUBLIC_EVENTS,
    WorldSceneService,
    select_scheduled_public_event,
)
from open_shift.store import WorldStore
from open_shift.world_events import (
    CHARACTER_STORY_ARCS,
    EVENT_AGENTS,
    EVENT_CATEGORIES,
    EVENT_STATUSES,
    PublicWorldEvent,
    character_story_arcs_for_day,
)


TOKEN = "world-event-test-token"


class WorldEventTests(unittest.TestCase):
    @staticmethod
    def _event(summary: str = "公共交通暂时绕开两个街区。") -> PublicWorldEvent:
        return PublicWorldEvent(
            "district_transit_alert",
            "city",
            "developing",
            "市中心交通线路临时调整",
            summary,
            ("alma", "stella"),
        )

    def test_public_event_is_idempotent_and_shared_with_tablet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WorldSceneService(Path(temp_dir) / "world.sqlite3", advance_minutes=0)
            event_id = service.publish_public_world_event(self._event())
            self.assertEqual(service.publish_public_world_event(self._event()), event_id)
            feed = service.tablet_feed({"limit": 5})
            self.assertEqual(feed["world_day"], 1)
            self.assertEqual(len(feed["items"]), 1)
            item = feed["items"][0]
            self.assertEqual(item["event_id"], event_id)
            self.assertEqual(item["event_key"], "district_transit_alert")
            self.assertEqual(item["affected_agents"], ["alma", "stella"])
            premise = service._event_premise(
                {
                    "event_id": event_id,
                    "event_type": "public_world_event",
                    "actor_id": "alma",
                    "target_id": None,
                    "payload": self._event().to_dict(),
                },
                {"alma": "Alma", "stella": "Stella"},
                0,
            )
            self.assertIn("市中心交通线路临时调整", premise)
            self.assertIn("公共交通", premise)

    def test_supported_provider_candidates_are_persisted_and_replayed(self) -> None:
        event = PublicWorldEvent(
            "llm_transit_update",
            "city",
            "active",
            "夜班公交临时改道",
            "施工让夜班公交今晚绕开酒吧所在街区。",
            ("alma", "stella"),
        )

        class CandidateProvider(MockProvider):
            calls = 0

            def generate_public_world_event_candidates(self, day, context):
                type(self).calls += 1
                return (event,)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                path, provider_factory=CandidateProvider, advance_minutes=0
            )
            with WorldStore(path) as store:
                store.set_meta("current_story_day", "2")
            service.prepare_story_day({"request_id": "candidate-1"})
            service.prepare_story_day({"request_id": "candidate-2"})
            with WorldStore(path) as store:
                self.assertEqual(CandidateProvider.calls, 1)
                receipt = json.loads(store.get_meta("llm_public_event_candidates:2"))
                selection = json.loads(store.get_meta("llm_public_event_selection:2"))
                self.assertEqual(receipt["candidates"][0]["event_key"], event.event_key)
                self.assertEqual(selection["event_key"], event.event_key)
                records = [item for item in store.list_events() if item["event_type"] == "public_world_event"]
                self.assertEqual(len(records), 1)

    def test_supported_provider_can_persist_day_thirteen_candidates(self) -> None:
        event = PublicWorldEvent(
            "llm_day_13_transit",
            "city",
            "developing",
            "凌晨线路临时调整",
            "运营方把一条凌晨线路改到旧城区，预计只持续几天。",
            ("alma", "sei"),
        )

        class CandidateProvider(MockProvider):
            calls = 0

            def generate_world_event_candidates(self, day, context):
                type(self).calls += 1
                return (event,)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                path, provider_factory=CandidateProvider, advance_minutes=0
            )
            with WorldStore(path) as store:
                store.set_meta("current_story_day", "13")
            service.prepare_story_day({"request_id": "day-13-1"})
            service.prepare_story_day({"request_id": "day-13-2"})
            with WorldStore(path) as store:
                self.assertEqual(CandidateProvider.calls, 1)
                candidates = json.loads(
                    store.get_meta("llm_public_event_candidates:13")
                )
                selection = json.loads(
                    store.get_meta("llm_public_event_selection:13")
                )
                self.assertEqual(
                    candidates["candidates"][0]["event_key"], event.event_key
                )
                self.assertEqual(selection["event_key"], event.event_key)
                records = [
                    item
                    for item in store.list_events()
                    if item["event_type"] == "public_world_event"
                ]
                self.assertEqual(len(records), 1)

    def test_invalid_provider_candidates_fall_back_to_code_pool(self) -> None:
        class InvalidProvider(MockProvider):
            calls = 0

            def generate_public_world_event_candidates(self, day, context):
                type(self).calls += 1
                return [{"events": []}]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                path, provider_factory=InvalidProvider, advance_minutes=0
            )
            with WorldStore(path) as store:
                store.set_meta("current_story_day", "2")
            service.prepare_story_day({"request_id": "fallback-1"})
            service.prepare_story_day({"request_id": "fallback-2"})
            with WorldStore(path) as store:
                self.assertEqual(InvalidProvider.calls, 1)
                self.assertIsNone(store.get_meta("llm_public_event_selection:2"))
                self.assertEqual(
                    json.loads(store.get_meta("llm_public_event_attempt:2"))["status"],
                    "fallback",
                )
                self.assertIsNotNone(store.get_meta("scheduled_public_event_selection:2"))
                records = [item for item in store.list_events() if item["event_type"] == "public_world_event"]
                self.assertEqual(len(records), 1)

    def test_reusing_world_event_key_with_changed_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WorldSceneService(Path(temp_dir) / "world.sqlite3", advance_minutes=0)
            service.publish_public_world_event(self._event())
            with self.assertRaisesRegex(BridgeError, "different content"):
                service.publish_public_world_event(self._event("线路已经恢复。"))

    def test_authenticated_tablet_feed_endpoint_is_strict_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WorldSceneService(Path(temp_dir) / "world.sqlite3", advance_minutes=0)
            service.publish_public_world_event(self._event())
            app = BridgeApplication(
                BridgeConfig(token=TOKEN, port=0),
                tablet_feed_handler=service.tablet_feed,
            )
            headers = {"X-Open-Shift-Token": TOKEN}
            request = {
                "protocol_version": 1,
                "request_id": "tablet-feed-1",
                "client_session_id": "tablet-session-1",
                "limit": 5.0,
            }
            first = app.handle(
                "POST", "/v1/tablet/feed", headers, json.dumps(request).encode()
            )
            replay = app.handle(
                "POST", "/v1/tablet/feed", headers, json.dumps(request).encode()
            )
            self.assertEqual(first.status, 200)
            self.assertEqual(replay.body, first.body)
            self.assertEqual(first.body["items"][0]["event_key"], "district_transit_alert")
            invalid = dict(request)
            invalid["request_id"] = "tablet-feed-2"
            invalid["limit"] = 9
            rejected = app.handle(
                "POST", "/v1/tablet/feed", headers, json.dumps(invalid).encode()
            )
            self.assertEqual(rejected.status, 400)
            self.assertEqual(rejected.body["error"]["code"], "invalid_feed_limit")

    def test_scheduled_catalogue_is_bounded_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WorldSceneService(Path(temp_dir) / "world.sqlite3", advance_minutes=0)
            with service._lock:
                from open_shift.store import WorldStore

                with WorldStore(service.db_path) as store:
                    store.set_meta("current_story_day", "2")
                    service._ensure_scheduled_public_event(store)
                    service._ensure_scheduled_public_event(store)
            feed = service.tablet_feed({"limit": 8})
            self.assertEqual(len(feed["items"]), 1)
            self.assertEqual(
                feed["items"][0]["event_key"],
                select_scheduled_public_event(2, 7).event_key,
            )

    def test_scheduled_event_selection_is_versioned_and_reproducible(self) -> None:
        first = select_scheduled_public_event(2, 7)
        self.assertIsNotNone(first)
        self.assertEqual(first, select_scheduled_public_event(2, 7))
        self.assertNotEqual(
            first,
            select_scheduled_public_event(2, 7, version="stage26-candidate-pool-v2"),
        )
        self.assertGreater(
            len({select_scheduled_public_event(2, seed).event_key for seed in range(1, 32)}),
            1,
        )
        self.assertGreater(
            len({select_scheduled_public_event(day, 7).event_key for day in range(2, 13)}),
            1,
        )
        self.assertEqual(
            SCHEDULED_PUBLIC_EVENT_VERSION,
            "stage26-candidate-pool-v1",
        )

    def test_scheduled_catalogue_covers_days_two_to_twelve_with_valid_unique_events(self) -> None:
        self.assertEqual(set(_SCHEDULED_PUBLIC_EVENTS), set(range(2, 13)))
        self.assertTrue(all(len(events) >= 3 for events in _SCHEDULED_PUBLIC_EVENTS.values()))
        events = [
            event
            for day_events in _SCHEDULED_PUBLIC_EVENTS.values()
            for event in day_events
        ]
        self.assertEqual(len({event.event_key for event in events}), len(events))
        self.assertTrue(all(event.category in EVENT_CATEGORIES for event in events))
        self.assertTrue(all(event.status in EVENT_STATUSES for event in events))
        self.assertTrue(
            all(
                event.affected_agents
                and set(event.affected_agents) <= EVENT_AGENTS
                for event in events
            )
        )

    def test_new_scheduled_event_is_materialized_once_and_used_as_story_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WorldSceneService(Path(temp_dir) / "world.sqlite3", advance_minutes=0)
            with service._lock:
                from open_shift.store import WorldStore

                with WorldStore(service.db_path) as store:
                    store.set_meta("current_story_day", "3")
                    service._ensure_scheduled_public_event(store)
                    service._ensure_scheduled_public_event(store)
                    records = store.list_events()
                    source_events = service._daily_source_events(records, 3)
            feed = service.tablet_feed({"limit": 8})
            self.assertEqual(feed["world_day"], 3)
            self.assertEqual(
                [item["event_key"] for item in feed["items"]],
                [select_scheduled_public_event(3, 7).event_key],
            )
            self.assertEqual(
                [
                    event["payload"]["event_key"]
                    for event in source_events
                    if event["event_type"] == "public_world_event"
                ],
                [select_scheduled_public_event(3, 7).event_key],
            )

    def test_first_day_uses_code_owned_fixed_tablet_articles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WorldSceneService(Path(temp_dir) / "world.sqlite3", advance_minutes=0)
            feed = service.tablet_feed({"limit": 8})
            self.assertEqual(len(feed["items"]), 3)
            self.assertEqual(
                {item["event_key"] for item in feed["items"]},
                {
                    "city_news_day_1_transit",
                    "city_news_day_1_night_market",
                    "city_news_day_1_weather",
                },
            )

    def test_public_event_is_used_as_a_daily_graph_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WorldSceneService(
                Path(temp_dir) / "world.sqlite3",
                advance_minutes=0,
                daily_story_mode=True,
                prefetch_days=0,
            )
            service.publish_public_world_event(self._event())
            graph = service.prepare_daily_story_graph(1)
            arrival_topics = [
                node.topic for node in graph.nodes if node.kind.value == "arrival_order"
            ]
            self.assertTrue(any("客户资料" in topic for topic in arrival_topics))

    def test_character_story_catalogue_has_distinct_multi_day_arcs(self) -> None:
        self.assertGreaterEqual(len(CHARACTER_STORY_ARCS), 6)
        self.assertGreaterEqual(
            {arc.owner_id for arc in CHARACTER_STORY_ARCS},
            {"alma", "sei", "stella", "dorothy", "dana"},
        )
        self.assertTrue(all(len(arc.stages) >= 2 for arc in CHARACTER_STORY_ARCS))
        day_one = character_story_arcs_for_day(1)
        self.assertEqual(len(day_one), len(CHARACTER_STORY_ARCS))
        self.assertEqual(len({arc.owner_id for arc, _ in day_one}), 5)

    def test_character_story_stage_is_safe_for_dialogue(self) -> None:
        arc, stage = character_story_arcs_for_day(1)[0]
        payload = __import__("open_shift.world_events", fromlist=["character_story_event"]).character_story_event(arc, stage)
        self.assertEqual(payload["event_key"], "alma_client_file_day_1")
        self.assertNotIn("story_arc_started", repr(payload))
        self.assertIn("facts", payload)

    def test_tablet_feed_deduplicates_event_keys_and_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WorldSceneService(Path(temp_dir) / "world.sqlite3", advance_minutes=0)
            event = self._event()
            with service._lock:
                from open_shift.store import WorldStore

                with WorldStore(service.db_path) as store, store.transaction():
                    store.append_event(0, "public_world_event", "alma", payload=event.to_dict())
                    duplicate_key = event.to_dict()
                    duplicate_key["summary"] = "同一事件的重复更新。"
                    store.append_event(1, "public_world_event", "alma", payload=duplicate_key)
                    duplicate_content = event.to_dict()
                    duplicate_content["event_key"] = "duplicate_content_key"
                    store.append_event(2, "public_world_event", "alma", payload=duplicate_content)
            feed = service.tablet_feed({"limit": 3})
            self.assertEqual(len(feed["items"]), 2)
            self.assertEqual(feed["items"][0]["summary"], event.summary)
            self.assertEqual(feed["items"][1]["summary"], "同一事件的重复更新。")

    def test_story_prepare_waits_for_a_ready_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WorldSceneService(
                Path(temp_dir) / "world.sqlite3",
                advance_minutes=0,
                daily_story_mode=True,
                prefetch_days=0,
            )
            result = service.prepare_story_day({"request_id": "prepare-1"})
            self.assertEqual(result["world_day"], 1)
            self.assertEqual(result["status"], "ready")
            self.assertFalse(result["opening_seen"])
            self.assertEqual(result["shift_phase"], "playing")
            self.assertEqual(result["last_completed_story_day"], 0)
            replay = service.prepare_story_day({"request_id": "prepare-2"})
            self.assertEqual(replay, result)

    def test_story_prepare_does_not_call_dialogue_provider(self) -> None:
        class CountingProvider(MockProvider):
            dialogue_calls = 0
            player_calls = 0

            @classmethod
            def generate_dialogue_line(cls, context):
                cls.dialogue_calls += 1
                return super().generate_dialogue_line(context)

            @classmethod
            def generate_player_dialogue_line(cls, context):
                cls.player_calls += 1
                return super().generate_player_dialogue_line(context)

        with tempfile.TemporaryDirectory() as temp_dir:
            service = WorldSceneService(
                Path(temp_dir) / "world.sqlite3",
                provider_factory=CountingProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            service.prepare_story_day({"request_id": "skeleton-only"})
            self.assertEqual(CountingProvider.dialogue_calls, 0)
            self.assertEqual(CountingProvider.player_calls, 0)


if __name__ == "__main__":
    unittest.main()
