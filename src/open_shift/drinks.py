"""Authoritative drink orders and recipe classification for player service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


_RESOURCE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
INGREDIENT_NAMES = (
    "adelhyde",
    "bronson_extract",
    "powdered_delta",
    "flanergide",
    "karmotrine",
)


class AlcoholRequirement(str, Enum):
    REQUIRED = "required"
    FORBIDDEN = "forbidden"
    ANY = "any"


class ServiceCategory(str, Enum):
    EXACT = "exact"
    ACCEPTABLE = "acceptable"
    WRONG = "wrong"
    SPECIAL = "special"


@dataclass(frozen=True, slots=True)
class DrinkOrder:
    order_id: str
    customer_id: str
    requested_drink_id: str
    requested_name: str
    preference_tags: tuple[str, ...]
    alcohol_requirement: AlcoholRequirement
    display_text: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.order_id, "order_id"),
            (self.customer_id, "customer_id"),
            (self.requested_drink_id, "requested_drink_id"),
        ):
            if not _RESOURCE_ID.fullmatch(value):
                raise ValueError(f"{label} was invalid")
        if not self.requested_name or len(self.requested_name) > 48:
            raise ValueError("requested_name was invalid")
        if not 1 <= len(self.preference_tags) <= 4:
            raise ValueError("preference_tags were invalid")
        if any(not _RESOURCE_ID.fullmatch(tag) for tag in self.preference_tags):
            raise ValueError("preference tag was invalid")
        if not self.display_text or len(self.display_text) > 160:
            raise ValueError("order display text was invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "customer_id": self.customer_id,
            "requested_drink_id": self.requested_drink_id,
            "requested_name": self.requested_name,
            "preference_tags": list(self.preference_tags),
            "alcohol_requirement": self.alcohol_requirement.value,
            "display_text": self.display_text,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DrinkOrder":
        required = {
            "order_id",
            "customer_id",
            "requested_drink_id",
            "requested_name",
            "preference_tags",
            "alcohol_requirement",
            "display_text",
        }
        if set(value) != required:
            raise ValueError("drink order fields did not match the schema")
        tags = value["preference_tags"]
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError("drink order preference_tags must be strings")
        strings = (
            value["order_id"],
            value["customer_id"],
            value["requested_drink_id"],
            value["requested_name"],
            value["alcohol_requirement"],
            value["display_text"],
        )
        if not all(isinstance(item, str) for item in strings):
            raise ValueError("drink order string fields were invalid")
        try:
            requirement = AlcoholRequirement(value["alcohol_requirement"])
        except ValueError:
            raise ValueError("drink order alcohol requirement was invalid") from None
        return cls(
            value["order_id"],
            value["customer_id"],
            value["requested_drink_id"],
            value["requested_name"],
            tuple(tags),
            requirement,
            value["display_text"],
        )


@dataclass(frozen=True, slots=True)
class DrinkSubmission:
    adelhyde: int
    bronson_extract: int
    powdered_delta: int
    flanergide: int
    karmotrine: int
    ice: bool
    aged: bool
    preparation: str

    def __post_init__(self) -> None:
        for name in INGREDIENT_NAMES:
            amount = getattr(self, name)
            if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= 20:
                raise ValueError(f"{name} must be an integer from 0 to 20")
        if self.preparation not in {"mixed", "blended"}:
            raise ValueError("preparation was invalid")

    @property
    def ingredients(self) -> tuple[int, int, int, int, int]:
        return tuple(getattr(self, name) for name in INGREDIENT_NAMES)  # type: ignore[return-value]

    def to_dict(self) -> dict[str, int | str]:
        return {
            **{name: getattr(self, name) for name in INGREDIENT_NAMES},
            "ice": int(self.ice),
            "aged": int(self.aged),
            "preparation": self.preparation,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DrinkSubmission":
        required = {*INGREDIENT_NAMES, "ice", "aged", "preparation"}
        if set(value) != required:
            raise ValueError("drink submission fields did not match the schema")
        amounts: list[int] = []
        for name in INGREDIENT_NAMES:
            raw_amount = value[name]
            if isinstance(raw_amount, bool):
                raise ValueError(f"{name} must be an integer from 0 to 20")
            if isinstance(raw_amount, int):
                amount = raw_amount
            elif isinstance(raw_amount, float) and raw_amount.is_integer():
                amount = int(raw_amount)
            else:
                raise ValueError(f"{name} must be an integer from 0 to 20")
            amounts.append(amount)
        for flag in ("ice", "aged"):
            if value[flag] not in (0, 1, False, True):
                raise ValueError(f"{flag} must be 0 or 1")
        if not isinstance(value["preparation"], str):
            raise ValueError("preparation must be a string")
        return cls(
            *amounts,
            ice=bool(value["ice"]),
            aged=bool(value["aged"]),
            preparation=value["preparation"],
        )


@dataclass(frozen=True, slots=True)
class DrinkRecipe:
    drink_id: str
    display_name: str
    ingredients: tuple[int, int, int, int, int]
    preparation: str
    ice: bool
    aged: bool
    flavor: str
    style: str
    price: int
    base_size: str = "normal"
    scalable: bool = True
    optional_karmotrine: bool = False


@dataclass(frozen=True, slots=True)
class ClassifiedDrink:
    drink_id: str
    display_name: str
    flavor: str
    style: str
    size: str
    alcoholic: bool
    doubled: bool
    price: int
    iced: bool = False

    @property
    def tags(self) -> frozenset[str]:
        tags = {self.flavor, self.style, self.size}
        if self.iced:
            tags.add("ice")
        if self.alcoholic:
            tags.add("strong")
        return frozenset(tags)


@dataclass(frozen=True, slots=True)
class ServiceResult:
    order_id: str
    customer_id: str
    category: ServiceCategory
    beverage_id: str
    beverage_name: str
    alcoholic: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "customer_id": self.customer_id,
            "category": self.category.value,
            "beverage_id": self.beverage_id,
            "beverage_name": self.beverage_name,
            "alcoholic": self.alcoholic,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ServiceResult":
        required = {
            "order_id",
            "customer_id",
            "category",
            "beverage_id",
            "beverage_name",
            "alcoholic",
        }
        if set(value) != required:
            raise ValueError("service result fields did not match the schema")
        if not all(isinstance(value[key], str) for key in required - {"alcoholic"}):
            raise ValueError("service result string fields were invalid")
        if not isinstance(value["alcoholic"], bool):
            raise ValueError("service result alcoholic flag was invalid")
        try:
            category = ServiceCategory(value["category"])
        except ValueError:
            raise ValueError("service category was invalid") from None
        return cls(
            value["order_id"],
            value["customer_id"],
            category,
            value["beverage_id"],
            value["beverage_name"],
            value["alcoholic"],
        )


def _recipe(
    drink_id: str,
    display_name: str,
    ingredients: tuple[int, int, int, int, int],
    preparation: str,
    *,
    ice: bool = False,
    aged: bool = False,
    flavor: str,
    style: str,
    price: int,
    base_size: str = "normal",
    scalable: bool = True,
    optional_karmotrine: bool = False,
) -> DrinkRecipe:
    return DrinkRecipe(
        drink_id,
        display_name,
        ingredients,
        preparation,
        ice,
        aged,
        flavor,
        style,
        price,
        base_size,
        scalable,
        optional_karmotrine,
    )


# These are rule data transcribed from the original mixer predicates. Runtime
# classification never trusts a model-provided drink name or success flag.
DRINK_RECIPES: tuple[DrinkRecipe, ...] = (
    _recipe("fmoai", "Flaming Moai", (1, 1, 2, 3, 5), "mixed", flavor="spicy", style="classy", price=150, base_size="big", scalable=False),
    _recipe("srush", "Sugar Rush", (2, 0, 1, 0, 0), "mixed", flavor="sweet", style="girly", price=150, optional_karmotrine=True),
    _recipe("sstar", "Sparkle Star", (2, 0, 1, 0, 0), "mixed", aged=True, flavor="sweet", style="girly", price=150, optional_karmotrine=True),
    _recipe("bfairy", "Blue Fairy", (4, 0, 0, 1, 0), "mixed", aged=True, flavor="sweet", style="girly", price=170, optional_karmotrine=True),
    _recipe("fdream", "Fluffy Dream", (3, 0, 3, 0, 0), "mixed", aged=True, flavor="sour", style="girly", price=170, optional_karmotrine=True),
    _recipe("scloud", "Sun Cloud", (2, 2, 0, 0, 0), "blended", ice=True, flavor="bitter", style="girly", price=150, optional_karmotrine=True),
    _recipe("moblast", "Moonblast", (6, 0, 1, 1, 2), "blended", ice=True, flavor="sweet", style="girly", price=180),
    _recipe("gpunch", "Gut Punch", (0, 5, 0, 1, 0), "mixed", aged=True, flavor="bitter", style="manly", price=80, optional_karmotrine=True),
    _recipe("pdriver", "Piledriver", (0, 3, 0, 3, 4), "mixed", flavor="bitter", style="manly", price=160),
    _recipe("splex", "Suplex", (0, 4, 0, 3, 3), "mixed", ice=True, flavor="bitter", style="manly", price=160),
    _recipe("mablast", "Marsblast", (0, 6, 1, 4, 2), "blended", flavor="spicy", style="manly", price=170, base_size="big", scalable=False),
    _recipe("cspike", "Crevice Spike", (0, 0, 2, 4, 0), "blended", flavor="sour", style="manly", price=140, optional_karmotrine=True),
    _recipe("beer", "Beer", (1, 2, 1, 2, 4), "mixed", flavor="bubbly", style="classic", price=200),
    _recipe("bjane", "Bleeding Jane", (0, 1, 3, 3, 0), "blended", flavor="spicy", style="classic", price=200),
    _recipe("fwater", "Frothy Water", (1, 1, 1, 1, 0), "mixed", aged=True, flavor="bubbly", style="classic", price=150),
    _recipe("btouch", "Bad Touch", (0, 2, 2, 2, 4), "mixed", ice=True, flavor="sour", style="classy", price=250),
    _recipe("btini", "Brandtini", (6, 0, 3, 0, 1), "mixed", aged=True, flavor="sweet", style="classy", price=250),
    _recipe("cvelvet", "Cobalt Velvet", (2, 0, 0, 3, 5), "mixed", ice=True, flavor="bubbly", style="classy", price=280),
    _recipe("fweaver", "Fringe Weaver", (1, 0, 0, 0, 9), "mixed", aged=True, flavor="bubbly", style="classy", price=260),
    _recipe("meblast", "Mercuryblast", (1, 1, 3, 3, 2), "blended", ice=True, flavor="sour", style="classy", price=250),
    _recipe("gtemple", "Grizzly Temple", (3, 3, 3, 0, 1), "blended", flavor="bitter", style="promo", price=220),
    _recipe("blight", "Bloom Light", (4, 0, 1, 2, 3), "mixed", ice=True, aged=True, flavor="spicy", style="promo", price=230),
    _recipe("zstar", "Zen Star", (4, 4, 4, 4, 4), "mixed", ice=True, flavor="sour", style="promo", price=210, base_size="big", scalable=False),
    _recipe("pman", "Piano Man", (2, 3, 5, 5, 3), "mixed", ice=True, flavor="bitter", style="promo", price=320, base_size="big", scalable=False),
    _recipe("pwman", "Piano Woman", (5, 5, 2, 3, 3), "mixed", aged=True, flavor="sweet", style="promo", price=320, base_size="big", scalable=False),
)


def _matches_recipe(
    submission: DrinkSubmission, recipe: DrinkRecipe, scale: int
) -> bool:
    if submission.preparation != recipe.preparation:
        return False
    if submission.ice != recipe.ice or submission.aged != recipe.aged:
        return False
    expected = tuple(amount * scale for amount in recipe.ingredients)
    actual = submission.ingredients
    if recipe.optional_karmotrine:
        return actual[:4] == expected[:4]
    return actual == expected


def classify_drink(submission: DrinkSubmission) -> ClassifiedDrink | None:
    for recipe in DRINK_RECIPES:
        scales = (1, 2) if recipe.scalable else (1,)
        for scale in scales:
            if not _matches_recipe(submission, recipe, scale):
                continue
            size = "big" if scale == 2 or recipe.base_size == "big" else "normal"
            return ClassifiedDrink(
                recipe.drink_id,
                recipe.display_name,
                recipe.flavor,
                recipe.style,
                size,
                submission.karmotrine > 0,
                scale == 2,
                recipe.price,
                recipe.ice,
            )
    return None


def _alcohol_matches(order: DrinkOrder, drink: ClassifiedDrink) -> bool:
    if order.alcohol_requirement is AlcoholRequirement.REQUIRED:
        return drink.alcoholic
    if order.alcohol_requirement is AlcoholRequirement.FORBIDDEN:
        return not drink.alcoholic
    return True


def evaluate_service(order: DrinkOrder, submission: DrinkSubmission) -> ServiceResult:
    drink = classify_drink(submission)
    if drink is None:
        return ServiceResult(
            order.order_id,
            order.customer_id,
            ServiceCategory.WRONG,
            "failed",
            "Unknown Drink",
            submission.karmotrine > 0,
        )
    alcohol_ok = _alcohol_matches(order, drink)
    if drink.drink_id == order.requested_drink_id and alcohol_ok:
        category = ServiceCategory.SPECIAL if drink.doubled else ServiceCategory.EXACT
    elif alcohol_ok and drink.tags.intersection(order.preference_tags):
        category = ServiceCategory.ACCEPTABLE
    else:
        category = ServiceCategory.WRONG
    return ServiceResult(
        order.order_id,
        order.customer_id,
        category,
        drink.drink_id,
        drink.display_name,
        drink.alcoholic,
    )


def service_income(result: ServiceResult, submission: DrinkSubmission) -> int:
    """Return the original game's authoritative payout for a served drink.

    The original mixer pays the classified drink's base price. A scalable
    doubled drink earns the normal big-glass surcharge of 100 gil; recipes
    that are inherently big are already represented by their base price.
    Wrong or unrecognized drinks earn nothing regardless of their recipe.
    """
    if result.category is ServiceCategory.WRONG:
        return 0
    drink = classify_drink(submission)
    if drink is None:
        return 0
    return drink.price + (100 if drink.doubled else 0)


@dataclass(frozen=True, slots=True)
class _OrderProfile:
    drink_id: str
    preference_tags: tuple[str, ...]
    request_variants: tuple[str, ...]
    alcohol_requirement: AlcoholRequirement = AlcoholRequirement.REQUIRED


# The original game exposes five flavor filters and five style filters. Keep
# each profile tied to a real recipe, then rotate profiles deterministically
# after the initial compatibility window so a new day can exercise every
# category without changing an already materialized early-day order.
_ORDER_PROFILES: dict[str, tuple[_OrderProfile, ...]] = {
    "dana": (
        _OrderProfile(
            "beer",
            ("bubbly", "classic", "strong"),
            (
                "Jill，来杯清爽的、带点泡沫，而且要有酒精。",
                "给我一杯 Beer，要有酒精，今天想喝点简单的。",
            ),
        ),
        _OrderProfile(
            "pdriver",
            ("bitter", "manly", "strong"),
            (
                "Jill，来点苦的、硬朗的，要有酒精。",
                "给我一杯 Piledriver，苦一点，酒精别省。",
            ),
        ),
        _OrderProfile(
            "bjane",
            ("spicy", "classic", "strong"),
            (
                "Jill，想喝辛辣又经典的，要有酒精。",
                "来一杯 Bleeding Jane，带酒精，辛辣一点。",
            ),
        ),
    ),
    "dorothy": (
        _OrderProfile(
            "pwman",
            ("sweet", "girly", "promo"),
            (
                "Jill，来点甜的，要有酒精，做得漂亮些。",
                "给我一杯 Piano Woman，甜口、带酒精，今晚要点气势。",
            ),
        ),
        _OrderProfile(
            "gtemple",
            ("bitter", "promo"),
            (
                "Jill，想喝苦一点的，要有酒精，来点特别的气势。",
                "给我一杯 Grizzly Temple，苦口而且带酒精。",
            ),
        ),
        _OrderProfile(
            "zstar",
            ("sour", "promo"),
            (
                "Jill，来点酸的，要有酒精，做得漂亮些。",
                "给我一杯 Zen Star，酸一点，酒精要有。",
            ),
        ),
    ),
    "alma": (
        _OrderProfile(
            "btini",
            ("sweet", "classy"),
            (
                "Jill，甜一点的，但要带酒精，别把味道做得太重。",
                "一杯 Brandtini，要有酒精，照老样子来。",
            ),
        ),
        _OrderProfile(
            "btouch",
            ("sour", "classy"),
            (
                "Jill，想喝酸一点、精致的，要有酒精。",
                "来一杯 Bad Touch，酸口、带酒精，别太随便。",
            ),
        ),
        _OrderProfile(
            "cvelvet",
            ("bubbly", "classy"),
            (
                "Jill，来杯带气泡的、精致的，要有酒精。",
                "给我一杯 Cobalt Velvet，带酒精，气泡感要在。",
            ),
        ),
    ),
    "stella": (
        _OrderProfile(
            "blight",
            ("spicy", "promo", "ice", "strong"),
            (
                "Jill，来杯冰多一点、带劲的，要有酒精。",
                "一杯 Bloom Light，冰别省掉，酒精也别省。",
            ),
        ),
        _OrderProfile(
            "meblast",
            ("sour", "classy", "ice"),
            (
                "Jill，想喝酸一点、冰的，要有酒精。",
                "给我一杯 Mercuryblast，酸口、带酒精，冰要足。",
            ),
        ),
        _OrderProfile(
            "cvelvet",
            ("bubbly", "classy", "ice"),
            (
                "Jill，来点气泡、冰的，要有酒精。",
                "给我一杯 Cobalt Velvet，带酒精，冰别忘了。",
            ),
        ),
    ),
    "sei": (
        _OrderProfile(
            "moblast",
            ("sweet", "girly"),
            (
                "Jill，我想喝点甜的，带酒精，最好有点女性化的感觉。",
                "给我一杯 Moonblast，做得漂亮些，而且要有酒精。",
            ),
        ),
        _OrderProfile(
            "fdream",
            ("sour", "girly"),
            (
                "Jill，来点酸的、女性化的，要有酒精。",
                "给我一杯 Fluffy Dream，酸口但要带酒精。",
            ),
        ),
        _OrderProfile(
            "scloud",
            ("bitter", "girly", "ice"),
            (
                "Jill，想喝苦一点、冰的，要有酒精。",
                "给我一杯 Sun Cloud，苦口、带酒精，冰多些。",
            ),
        ),
    ),
}


def order_for_customer(customer_id: str, event_id: int) -> DrinkOrder:
    try:
        profiles = _ORDER_PROFILES[customer_id]
    except KeyError:
        raise ValueError("customer did not have a drink order blueprint") from None
    # Existing DAY1 records use the original profile. Later event ids rotate
    # through the expanded category set while remaining deterministic.
    profile_index = 0 if event_id < 20 else (event_id - 20) % len(profiles)
    profile = profiles[profile_index]
    recipe = next(item for item in DRINK_RECIPES if item.drink_id == profile.drink_id)
    display_text = profile.request_variants[event_id % len(profile.request_variants)]
    return DrinkOrder(
        f"order_{event_id}",
        customer_id,
        profile.drink_id,
        recipe.display_name,
        profile.preference_tags,
        profile.alcohol_requirement,
        display_text,
    )
