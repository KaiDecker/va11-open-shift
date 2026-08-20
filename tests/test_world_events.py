from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from open_shift.bridge import BridgeApplication, BridgeConfig, BridgeError
from open_shift.world_bridge import WorldSceneService
from open_shift.world_events import PublicWorldEvent


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
            self.assertEqual(feed["items"][0]["event_key"], "city_transit_day_2")

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
            self.assertTrue(any("市中心交通线路临时调整" in topic for topic in arrival_topics))

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


if __name__ == "__main__":
    unittest.main()
