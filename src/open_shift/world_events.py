"""Validated public events shared by bar stories and Jill's tablet feed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


_EVENT_KEY = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
EVENT_CATEGORIES = frozenset(
    {"city", "security", "technology", "health", "culture", "economy", "local"}
)
EVENT_STATUSES = frozenset({"developing", "active", "resolved"})
EVENT_AGENTS = frozenset({"dana", "dorothy", "alma", "stella", "sei"})



@dataclass(frozen=True, slots=True)
class PublicWorldEvent:
    event_key: str
    category: str
    status: str
    headline: str
    summary: str
    affected_agents: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _EVENT_KEY.fullmatch(self.event_key):
            raise ValueError("world event key was invalid")
        if self.category not in EVENT_CATEGORIES:
            raise ValueError("world event category was invalid")
        if self.status not in EVENT_STATUSES:
            raise ValueError("world event status was invalid")
        if not 1 <= len(self.headline) <= 96 or any(ord(char) < 32 for char in self.headline):
            raise ValueError("world event headline was invalid")
        if not 1 <= len(self.summary) <= 240 or any(ord(char) < 32 for char in self.summary):
            raise ValueError("world event summary was invalid")
        if len(set(self.affected_agents)) != len(self.affected_agents):
            raise ValueError("world event affected agents were duplicated")
        if any(agent not in EVENT_AGENTS for agent in self.affected_agents):
            raise ValueError("world event affected agent was invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "category": self.category,
            "status": self.status,
            "headline": self.headline,
            "summary": self.summary,
            "affected_agents": list(self.affected_agents),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicWorldEvent":
        required = {
            "event_key",
            "category",
            "status",
            "headline",
            "summary",
            "affected_agents",
        }
        if set(value) != required:
            raise ValueError("world event fields did not match the schema")
        agents = value["affected_agents"]
        strings = [value[key] for key in required - {"affected_agents"}]
        if not all(isinstance(item, str) for item in strings):
            raise ValueError("world event text fields were invalid")
        if not isinstance(agents, list) or not all(isinstance(item, str) for item in agents):
            raise ValueError("world event affected_agents was invalid")
        return cls(
            value["event_key"],
            value["category"],
            value["status"],
            value["headline"],
            value["summary"],
            tuple(agents),
        )


def tablet_feed_item(event_id: int, tick: int, event: PublicWorldEvent) -> dict[str, Any]:
    if event_id < 1 or tick < 0:
        raise ValueError("persisted world event identity was invalid")
    return {
        "event_id": event_id,
        "event_key": event.event_key,
        "category": event.category,
        "status": event.status,
        "headline": event.headline,
        "summary": event.summary,
        "occurred_tick": tick,
        "affected_agents": list(event.affected_agents),
    }


CODE_OWNED_DAY_ONE_EVENTS = (
    PublicWorldEvent(
        "city_news_day_1_transit",
        "city",
        "developing",
        "市中心交通线路临时调整",
        "施工封闭让两条常用线路绕开酒吧附近街区，预计几天内逐步恢复。",
        ("alma", "stella"),
    ),
    PublicWorldEvent(
        "city_news_day_1_night_market",
        "economy",
        "active",
        "夜间市场重新开放部分摊位",
        "街区商户试着恢复晚间营业，熟悉的招牌和新的摊位一起亮起了灯。",
        ("dana", "sei"),
    ),
    PublicWorldEvent(
        "city_news_day_1_weather",
        "local",
        "resolved",
        "连续降雨暂时停歇",
        "气象台预计今晚会短暂放晴，城市的维修队抓紧处理积水路段。",
        ("dorothy", "alma"),
    ),
)
