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
class CharacterStoryStage:
    """A compact canon beat; dialogue is still generated at runtime."""

    day: int
    facts: str
    stake: str
    stance: str
    choice: str
    follow_up: str

    def __post_init__(self) -> None:
        if self.day < 1 or self.day > 30:
            raise ValueError("character story stage day was invalid")
        fields = (self.facts, self.stake, self.stance, self.choice, self.follow_up)
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise ValueError("character story stage text was invalid")
        if any(len(value) > 240 for value in fields):
            raise ValueError("character story stage text was too long")

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "facts": self.facts,
            "stake": self.stake,
            "stance": self.stance,
            "choice": self.choice,
            "follow_up": self.follow_up,
        }


@dataclass(frozen=True, slots=True)
class CharacterStoryArc:
    """Persistent story facts shared by several days and characters."""

    arc_id: str
    owner_id: str
    counterpart_id: str
    title: str
    category: str
    stages: tuple[CharacterStoryStage, ...]

    def __post_init__(self) -> None:
        if not _EVENT_KEY.fullmatch(self.arc_id):
            raise ValueError("character story arc id was invalid")
        if self.owner_id not in EVENT_AGENTS or self.counterpart_id not in EVENT_AGENTS:
            raise ValueError("character story arc agent was invalid")
        if self.owner_id == self.counterpart_id or not self.title.strip():
            raise ValueError("character story arc identity was invalid")
        if not self.stages or len({stage.day for stage in self.stages}) != len(self.stages):
            raise ValueError("character story arc stages were invalid")
        if tuple(sorted(stage.day for stage in self.stages)) != tuple(stage.day for stage in self.stages):
            raise ValueError("character story arc stages were not ordered")


def character_story_event(arc: CharacterStoryArc, stage: CharacterStoryStage) -> dict[str, Any]:
    """Serialize one stage as safe event data for the existing event ledger."""

    return {
        "event_key": f"{arc.arc_id}_day_{stage.day}",
        "arc_id": arc.arc_id,
        "stage_day": stage.day,
        "owner_id": arc.owner_id,
        "counterpart_id": arc.counterpart_id,
        "title": arc.title,
        "headline": arc.title,
        "summary": stage.facts,
        "category": arc.category,
        "facts": stage.facts,
        "stake": stage.stake,
        "stance": stage.stance,
        "choice": stage.choice,
        "follow_up": stage.follow_up,
        "affected_agents": [arc.owner_id, arc.counterpart_id],
    }



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


# These are story facts and choices, not dialogue.  The provider may phrase
# them differently, but it cannot invent the canon or make every customer
# discuss the same city headline.
CHARACTER_STORY_ARCS = (
    CharacterStoryArc(
        "alma_client_file",
        "alma",
        "dana",
        "客户资料的去向",
        "work",
        (
            CharacterStoryStage(1, "Alma 发现客户资料被转交给了错误的联系人。", "她担心客户把这看成自己的疏忽。", "她不愿在查清来源前先道歉。", "先联系客户，还是先查清资料从哪里出去？", "Dana 可能认识接收资料的那家公司。"),
            CharacterStoryStage(2, "对方公司承认收到了资料，却说不清是谁转交的。", "如果继续追查，Alma 可能失去这笔业务。", "她开始怀疑这不是普通的系统错误。", "要不要把这件事正式上报？", "一份旧的交接记录留下了时间线。"),
            CharacterStoryStage(3, "旧交接记录显示，资料在一次临时换班后被重新标记。", "追究责任会牵连一个刚接手工作的同事。", "她想保护同事，但也不能替别人承担责任。", "先私下确认，还是直接提交记录？", "Dana 会要求她作出明确选择。"),
            CharacterStoryStage(4, "Alma 找到了能解释误传原因的完整记录。", "她必须决定是否把记录交给客户。", "她更在意把事实说清楚，而不是保住表面上的体面。", "现在说明全部情况，还是只说明已经修正的部分？", "这次选择会改变她和 Dana 对彼此的信任。"),
        ),
    ),
    CharacterStoryArc(
        "sei_night_route",
        "sei",
        "stella",
        "夜市路线上的约定",
        "work",
        (
            CharacterStoryStage(1, "夜市重新开放后，明晚接人的路线会经过更拥挤的街区。", "人流会让他无法同时照看约定的人和退路。", "他不想因为小变化就让别人替他担心。", "换一条路，还是提前出发？", "Stella 似乎知道那条路线最近发生过什么。"),
            CharacterStoryStage(2, "有人建议他照旧走旧路线，并承诺会清理沿途的障碍。", "相信这个承诺可能让接应的人暴露。", "他只相信自己亲眼确认过的路线。", "要不要接受这次保证？", "Stella 留下了一条不能公开转发的消息。"),
            CharacterStoryStage(3, "Stella 的消息证实旧路线确实有人在等着观察。", "改变路线会让真正的接头人找不到他。", "他不喜欢临时变更，但更不愿拿别人冒险。", "提前通知，还是保持原计划等对方出现？", "一旦决定，今晚就没有第二次尝试。"),
            CharacterStoryStage(4, "接应顺利完成，但对方问起他为何突然改变路线。", "解释太多会暴露 Stella 的消息来源。", "他选择替朋友把责任留在自己身上。", "要不要把这件事告诉 Stella？", "两人的信任会取决于他怎么说。"),
        ),
    ),
    CharacterStoryArc(
        "dorothy_missed_meeting",
        "dorothy",
        "alma",
        "被耽误的约见",
        "relationship",
        (
            CharacterStoryStage(1, "积水让 Dorothy 错过了原定的约见。", "她担心对方会把迟到理解成不在乎。", "她嘴上说只是天气问题，心里却想补救。", "恢复约见，还是先等对方主动？", "Alma 可能听说了对方最近的安排。"),
            CharacterStoryStage(2, "对方回复得很客气，却没有提出新的时间。", "再追问可能显得她在强求。", "她不想把一次迟到解释成一长串借口。", "发一条简短的消息，还是暂时不打扰？", "一张被雨水弄皱的便签还留在她手里。"),
            CharacterStoryStage(3, "Alma 提醒她，对方曾提过一个只在本周有效的机会。", "错过这周，见面可能就失去意义。", "她开始承认自己害怕听到拒绝。", "现在约最后一次，还是就此放下？", "Dorothy 必须自己承担这次选择。"),
            CharacterStoryStage(4, "Dorothy 终于收到一个明确的回复。", "答案无论好坏，都会结束这段悬着的等待。", "她决定不再用玩笑掩饰自己的认真。", "要不要当面把真正想说的话说出来？", "这段关系会进入新的状态。"),
        ),
    ),
    CharacterStoryArc(
        "stella_old_promise",
        "stella",
        "sei",
        "没有兑现的承诺",
        "relationship",
        (
            CharacterStoryStage(1, "Stella 发现 Sei 还没有兑现之前答应她的一件小事。", "她担心继续追问会让对方把这看成不信任。", "她不想替 Sei 找借口，却也不想直接翻脸。", "今晚提醒他，还是再等一天？", "Sei 最近的工作安排比他说的更紧。"),
            CharacterStoryStage(2, "Sei 解释自己确实尝试过，却被临时任务打断。", "Stella 需要判断这次解释是否值得相信。", "她愿意听理由，但不接受含糊的承诺。", "要不要让他给出一个明确时间？", "一份路线安排让她看见他漏掉的部分。"),
            CharacterStoryStage(3, "Stella 找到的安排显示，Sei 其实替别人承担了工作。", "责备他会伤到关系，沉默又会让问题继续。", "她决定把不满说清楚，但不替他做决定。", "现在把话说开，还是等任务结束？", "他们需要重新约定彼此能做到的事。"),
            CharacterStoryStage(4, "Sei 完成了承诺，却承认自己一开始就高估了时间。", "Stella 必须决定以后是否继续把重要的事交给他。", "她更看重坦白，而不是一次漂亮的补救。", "重新约定边界，还是保持原来的距离？", "两人会把这次教训带进下一次合作。"),
        ),
    ),
    CharacterStoryArc(
        "dana_shop_ledger",
        "dana",
        "dorothy",
        "酒吧账本里的缺口",
        "work",
        (
            CharacterStoryStage(1, "Dana 在酒吧账本里发现一笔无法对应到订单的支出。", "她要确认是记账疏漏，还是有人动过备用金。", "她不打算在没有证据时责怪任何人。", "先自己核对，还是问 Dorothy 是否见过那张单据？", "Dorothy 可能记得那天谁最后离开酒吧。"),
            CharacterStoryStage(2, "备用金记录和进货清单之间出现了一个时间差。", "继续追查会让员工觉得她在不信任大家。", "她把账看得很重，但不愿把酒吧变成审讯室。", "要不要把记录摊开给大家看？", "一位常客提到当天有人临时借用了电话。"),
            CharacterStoryStage(3, "那笔支出最终指向一次替客人垫付的紧急交通费。", "公开这件事可能让被帮助的人难堪。", "Dana 认为账要对上，但隐私也不能随便牺牲。", "只修正账目，还是把来龙去脉告诉 Dorothy？", "她需要 Dorothy 帮忙决定分寸。"),
            CharacterStoryStage(4, "Dana 补齐了账目，也决定把备用金规则写得更清楚。", "规则太严会让酒吧失去原来的温度。", "她想守住秩序，同时给人犯错和求助的空间。", "要不要把新规则交给大家一起修改？", "这会成为酒吧下一段日常的背景。"),
        ),
    ),
    CharacterStoryArc(
        "dana_late_message",
        "dana",
        "stella",
        "没有及时送出的消息",
        "relationship",
        (
            CharacterStoryStage(1, "Dana 写好一条重要消息，却一直没有按下发送。", "她担心消息一旦发出，就无法收回对关系的影响。", "她不喜欢把犹豫留给别人，却也不想假装确定。", "今晚发出去，还是等见面再说？", "Stella 可能比 Dana 更早知道那件事。"),
            CharacterStoryStage(2, "Stella 已经从别处听到一部分消息。", "隐瞒下去会显得 Dana 在故意控制信息。", "Dana 开始考虑直接承认自己的犹豫。", "先解释为什么没发，还是直接说重点？", "对方真正介意的也许不是消息本身。"),
            CharacterStoryStage(3, "谈话中出现了两种互相矛盾的说法。", "Dana 必须承认自己掌握的信息并不完整。", "她宁愿承认不知道，也不愿拿猜测当结论。", "要不要把未确认的部分也说出来？", "Stella 的反应会暴露她真正担心的事。"),
            CharacterStoryStage(4, "Dana 和 Stella 终于对齐了各自听到的事实。", "坦白并没有立刻解决问题，但让两人重新站在同一边。", "Dana 决定以后少替别人预判答案。", "要不要现在把那条消息发出去？", "这次选择会结束长期的误会。"),
        ),
    ),
)


def character_story_arcs_for_day(day: int) -> tuple[tuple[CharacterStoryArc, CharacterStoryStage], ...]:
    """Return the deterministic stage for each seed arc on a given day."""

    return tuple(
        (arc, stage)
        for arc in CHARACTER_STORY_ARCS
        for stage in arc.stages
        if stage.day == day
    )
