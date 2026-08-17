"""Versioned, bounded daily story graphs for prefetched player shifts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .bridge import AGENT_SPEAKERS, ScenePackage
from .drinks import ServiceCategory


DAILY_STORY_GRAPH_VERSION = "stage_7a_v1"
MAX_DAILY_CUSTOMERS = 3
_RESOURCE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class StoryNodeKind(str, Enum):
    ARRIVAL_ORDER = "arrival_order"
    RESULT_DIALOGUE = "result_dialogue"
    MERGE = "merge"


@dataclass(frozen=True, slots=True)
class StoryGraphNode:
    node_id: str
    kind: StoryNodeKind
    customer_id: str | None
    topic: str
    scene: ScenePackage | None = None
    service_category: ServiceCategory | None = None
    next_node_id: str | None = None
    branch_targets: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not _RESOURCE_ID.fullmatch(self.node_id):
            raise ValueError("story node_id was invalid")
        if self.customer_id is not None and self.customer_id not in AGENT_SPEAKERS:
            raise ValueError("story customer_id was invalid")
        if len(self.topic) > 240 or any(ord(char) < 32 for char in self.topic):
            raise ValueError("story topic was invalid")
        if self.next_node_id is not None and not _RESOURCE_ID.fullmatch(
            self.next_node_id
        ):
            raise ValueError("story next_node_id was invalid")
        targets = dict(self.branch_targets)
        if len(targets) != len(self.branch_targets) or any(
            not _RESOURCE_ID.fullmatch(target) for target in targets.values()
        ):
            raise ValueError("story branch targets were invalid")

        if self.kind is StoryNodeKind.ARRIVAL_ORDER:
            if self.customer_id is None or not self.topic or self.scene is None:
                raise ValueError("arrival order node was incomplete")
            if self.scene.order is None or self.scene.order.customer_id != self.customer_id:
                raise ValueError("arrival order node did not contain its customer order")
            if self.service_category is not None or self.next_node_id is not None:
                raise ValueError("arrival order node had invalid routing")
            if set(targets) != {category.value for category in ServiceCategory}:
                raise ValueError("arrival order node did not have all result branches")
        elif self.kind is StoryNodeKind.RESULT_DIALOGUE:
            if (
                self.customer_id is None
                or not self.topic
                or self.scene is None
                or self.scene.order is not None
                or self.service_category is None
                or self.next_node_id is None
                or targets
            ):
                raise ValueError("result dialogue node was incomplete")
        elif self.kind is StoryNodeKind.MERGE:
            if (
                self.customer_id is not None
                or self.scene is not None
                or self.service_category is not None
                or targets
                or self.topic
            ):
                raise ValueError("merge node contained candidate content")

    def targets(self) -> tuple[str, ...]:
        if self.kind is StoryNodeKind.ARRIVAL_ORDER:
            return tuple(target for _, target in self.branch_targets)
        return (self.next_node_id,) if self.next_node_id is not None else ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "customer_id": self.customer_id,
            "topic": self.topic,
            "scene": self.scene.to_dict() if self.scene is not None else None,
            "service_category": (
                self.service_category.value
                if self.service_category is not None
                else None
            ),
            "next_node_id": self.next_node_id,
            "branch_targets": dict(self.branch_targets),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StoryGraphNode":
        required = {
            "node_id",
            "kind",
            "customer_id",
            "topic",
            "scene",
            "service_category",
            "next_node_id",
            "branch_targets",
        }
        if set(value) != required:
            raise ValueError("story node fields did not match the schema")
        for key in ("node_id", "kind", "topic"):
            if not isinstance(value[key], str):
                raise ValueError("story node string fields were invalid")
        for key in ("customer_id", "service_category", "next_node_id"):
            if value[key] is not None and not isinstance(value[key], str):
                raise ValueError("story node optional fields were invalid")
        raw_scene = value["scene"]
        if raw_scene is not None and not isinstance(raw_scene, Mapping):
            raise ValueError("story node scene was invalid")
        raw_targets = value["branch_targets"]
        if not isinstance(raw_targets, Mapping) or not all(
            isinstance(key, str) and isinstance(target, str)
            for key, target in raw_targets.items()
        ):
            raise ValueError("story branch target fields were invalid")
        try:
            kind = StoryNodeKind(value["kind"])
            category = (
                ServiceCategory(value["service_category"])
                if value["service_category"] is not None
                else None
            )
        except ValueError:
            raise ValueError("story node enum value was invalid") from None
        return cls(
            value["node_id"],
            kind,
            value["customer_id"],
            value["topic"],
            ScenePackage.from_dict(raw_scene) if raw_scene is not None else None,
            category,
            value["next_node_id"],
            tuple(
                (category.value, raw_targets[category.value])
                for category in ServiceCategory
                if category.value in raw_targets
            ),
        )


@dataclass(frozen=True, slots=True)
class DailyStoryGraph:
    graph_id: str
    day_index: int
    generation_version: str
    source_tick: int
    source_event_ids: tuple[int, ...]
    entry_node_id: str
    terminal_node_id: str
    nodes: tuple[StoryGraphNode, ...]

    def __post_init__(self) -> None:
        if not _RESOURCE_ID.fullmatch(self.graph_id):
            raise ValueError("daily story graph_id was invalid")
        if (
            isinstance(self.day_index, bool)
            or not isinstance(self.day_index, int)
            or self.day_index < 1
            or isinstance(self.source_tick, bool)
            or not isinstance(self.source_tick, int)
            or self.source_tick < 0
        ):
            raise ValueError("daily story graph position was invalid")
        if not _RESOURCE_ID.fullmatch(self.generation_version):
            raise ValueError("daily story generation_version was invalid")
        if not 1 <= len(self.source_event_ids) <= MAX_DAILY_CUSTOMERS:
            raise ValueError("daily story source events were invalid")
        if len(set(self.source_event_ids)) != len(self.source_event_ids) or any(
            isinstance(event_id, bool)
            or not isinstance(event_id, int)
            or event_id < 1
            for event_id in self.source_event_ids
        ):
            raise ValueError("daily story source event identifiers were invalid")
        by_id = {node.node_id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise ValueError("daily story node identifiers were not unique")
        if self.entry_node_id not in by_id or self.terminal_node_id not in by_id:
            raise ValueError("daily story entry or terminal node was missing")
        if by_id[self.entry_node_id].kind is not StoryNodeKind.ARRIVAL_ORDER:
            raise ValueError("daily story entry node was invalid")
        arrivals = [
            node for node in self.nodes if node.kind is StoryNodeKind.ARRIVAL_ORDER
        ]
        results = [
            node for node in self.nodes if node.kind is StoryNodeKind.RESULT_DIALOGUE
        ]
        merges = [node for node in self.nodes if node.kind is StoryNodeKind.MERGE]
        if len(arrivals) != len(self.source_event_ids):
            raise ValueError("daily story arrival count did not match source events")
        if len(results) != len(arrivals) * len(ServiceCategory):
            raise ValueError("daily story result count was invalid")
        if len(merges) != len(arrivals) or len(self.nodes) != len(arrivals) * 6:
            raise ValueError("daily story graph was not finitely bounded")
        terminal = by_id[self.terminal_node_id]
        if terminal.kind is not StoryNodeKind.MERGE or terminal.next_node_id is not None:
            raise ValueError("daily story terminal node was invalid")
        for node in self.nodes:
            if any(target not in by_id for target in node.targets()):
                raise ValueError("daily story graph referenced a missing node")
            if (
                node.kind is StoryNodeKind.MERGE
                and node.next_node_id is not None
                and by_id[node.next_node_id].kind is not StoryNodeKind.ARRIVAL_ORDER
            ):
                raise ValueError("daily story merge did not lead to an arrival")

        claimed_results: set[str] = set()
        claimed_merges: set[str] = set()
        for arrival in arrivals:
            targets = dict(arrival.branch_targets)
            result_nodes = [by_id[targets[category.value]] for category in ServiceCategory]
            if len({node.node_id for node in result_nodes}) != len(ServiceCategory):
                raise ValueError("daily story result branches were not unique")
            merge_ids: set[str] = set()
            for category, result in zip(ServiceCategory, result_nodes, strict=True):
                if (
                    result.kind is not StoryNodeKind.RESULT_DIALOGUE
                    or result.customer_id != arrival.customer_id
                    or result.service_category is not category
                    or result.next_node_id is None
                    or by_id[result.next_node_id].kind is not StoryNodeKind.MERGE
                ):
                    raise ValueError("daily story result branch was inconsistent")
                claimed_results.add(result.node_id)
                merge_ids.add(result.next_node_id)
            if len(merge_ids) != 1:
                raise ValueError("daily story result branches did not converge")
            claimed_merges.update(merge_ids)
        if claimed_results != {node.node_id for node in results} or claimed_merges != {
            node.node_id for node in merges
        }:
            raise ValueError("daily story graph contained unclaimed branch nodes")

        visited: set[str] = set()
        active: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in active:
                raise ValueError("daily story graph contained a cycle")
            if node_id in visited:
                return
            active.add(node_id)
            for target in by_id[node_id].targets():
                visit(target)
            active.remove(node_id)
            visited.add(node_id)

        visit(self.entry_node_id)
        if visited != set(by_id):
            raise ValueError("daily story graph contained unreachable nodes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "day_index": self.day_index,
            "generation_version": self.generation_version,
            "source_tick": self.source_tick,
            "source_event_ids": list(self.source_event_ids),
            "entry_node_id": self.entry_node_id,
            "terminal_node_id": self.terminal_node_id,
            "nodes": [node.to_dict() for node in self.nodes],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DailyStoryGraph":
        required = {
            "graph_id",
            "day_index",
            "generation_version",
            "source_tick",
            "source_event_ids",
            "entry_node_id",
            "terminal_node_id",
            "nodes",
        }
        if set(value) != required:
            raise ValueError("daily story graph fields did not match the schema")
        for key in (
            "graph_id",
            "generation_version",
            "entry_node_id",
            "terminal_node_id",
        ):
            if not isinstance(value[key], str):
                raise ValueError("daily story graph string fields were invalid")
        if (
            isinstance(value["day_index"], bool)
            or not isinstance(value["day_index"], int)
            or isinstance(value["source_tick"], bool)
            or not isinstance(value["source_tick"], int)
        ):
            raise ValueError("daily story graph numeric fields were invalid")
        source_event_ids = value["source_event_ids"]
        nodes = value["nodes"]
        if not isinstance(source_event_ids, list) or not all(
            isinstance(item, int) and not isinstance(item, bool)
            for item in source_event_ids
        ):
            raise ValueError("daily story source event fields were invalid")
        if not isinstance(nodes, list) or not all(
            isinstance(node, Mapping) for node in nodes
        ):
            raise ValueError("daily story graph nodes were invalid")
        return cls(
            value["graph_id"],
            value["day_index"],
            value["generation_version"],
            value["source_tick"],
            tuple(source_event_ids),
            value["entry_node_id"],
            value["terminal_node_id"],
            tuple(StoryGraphNode.from_dict(node) for node in nodes),
        )
