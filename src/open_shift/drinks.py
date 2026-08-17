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

    @property
    def tags(self) -> frozenset[str]:
        return frozenset({self.flavor, self.style, self.size})


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
        base_size,
        scalable,
        optional_karmotrine,
    )


# These are rule data transcribed from the original mixer predicates. Runtime
# classification never trusts a model-provided drink name or success flag.
DRINK_RECIPES: tuple[DrinkRecipe, ...] = (
    _recipe("fmoai", "Flaming Moai", (1, 1, 2, 3, 5), "mixed", flavor="spicy", style="classy", base_size="big", scalable=False),
    _recipe("srush", "Sugar Rush", (2, 0, 1, 0, 0), "mixed", flavor="sweet", style="girly", optional_karmotrine=True),
    _recipe("sstar", "Sparkle Star", (2, 0, 1, 0, 0), "mixed", aged=True, flavor="sweet", style="girly", optional_karmotrine=True),
    _recipe("bfairy", "Blue Fairy", (4, 0, 0, 1, 0), "mixed", aged=True, flavor="sweet", style="girly", optional_karmotrine=True),
    _recipe("fdream", "Fluffy Dream", (3, 0, 3, 0, 0), "mixed", aged=True, flavor="sour", style="girly", optional_karmotrine=True),
    _recipe("scloud", "Sun Cloud", (2, 2, 0, 0, 0), "blended", ice=True, flavor="bitter", style="girly", optional_karmotrine=True),
    _recipe("moblast", "Moonblast", (6, 0, 1, 1, 2), "blended", ice=True, flavor="sweet", style="girly"),
    _recipe("gpunch", "Gut Punch", (0, 5, 0, 1, 0), "mixed", aged=True, flavor="bitter", style="manly", optional_karmotrine=True),
    _recipe("pdriver", "Piledriver", (0, 3, 0, 3, 4), "mixed", flavor="bitter", style="manly"),
    _recipe("splex", "Suplex", (0, 4, 0, 3, 3), "mixed", ice=True, flavor="bitter", style="manly"),
    _recipe("mablast", "Marsblast", (0, 6, 1, 4, 2), "blended", flavor="spicy", style="manly", base_size="big", scalable=False),
    _recipe("cspike", "Crevice Spike", (0, 0, 2, 4, 0), "blended", flavor="sour", style="manly", optional_karmotrine=True),
    _recipe("beer", "Beer", (1, 2, 1, 2, 4), "mixed", flavor="bubbly", style="classic"),
    _recipe("bjane", "Bleeding Jane", (0, 1, 3, 3, 0), "blended", flavor="spicy", style="classic"),
    _recipe("fwater", "Frothy Water", (1, 1, 1, 1, 0), "mixed", aged=True, flavor="bubbly", style="classic"),
    _recipe("btouch", "Bad Touch", (0, 2, 2, 2, 4), "mixed", ice=True, flavor="sour", style="classy"),
    _recipe("btini", "Brandtini", (6, 0, 3, 0, 1), "mixed", aged=True, flavor="sweet", style="classy"),
    _recipe("cvelvet", "Cobalt Velvet", (2, 0, 0, 3, 5), "mixed", ice=True, flavor="bubbly", style="classy"),
    _recipe("fweaver", "Fringe Weaver", (1, 0, 0, 0, 9), "mixed", aged=True, flavor="bubbly", style="classy"),
    _recipe("meblast", "Mercuryblast", (1, 1, 3, 3, 2), "blended", ice=True, flavor="sour", style="classy"),
    _recipe("gtemple", "Grizzly Temple", (3, 3, 3, 0, 1), "blended", flavor="bitter", style="promo"),
    _recipe("blight", "Bloom Light", (4, 0, 1, 2, 3), "mixed", ice=True, aged=True, flavor="spicy", style="promo"),
    _recipe("zstar", "Zen Star", (4, 4, 4, 4, 4), "mixed", ice=True, flavor="sour", style="promo", base_size="big", scalable=False),
    _recipe("pman", "Piano Man", (2, 3, 5, 5, 3), "mixed", ice=True, flavor="bitter", style="promo", base_size="big", scalable=False),
    _recipe("pwman", "Piano Woman", (5, 5, 2, 3, 3), "mixed", aged=True, flavor="sweet", style="promo", base_size="big", scalable=False),
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


_ORDER_BLUEPRINTS: dict[str, tuple[str, tuple[str, ...], AlcoholRequirement, str]] = {
    "dana": (
        "beer",
        ("bubbly", "classic"),
        AlcoholRequirement.REQUIRED,
        "Jill，给我一杯 Beer。老板偶尔也有当顾客的权利。",
    ),
    "dorothy": (
        "pwman",
        ("sweet", "promo"),
        AlcoholRequirement.REQUIRED,
        "Jill，来一杯 Piano Woman。今晚需要一点漂亮的气势。",
    ),
    "alma": (
        "btini",
        ("sweet", "classy"),
        AlcoholRequirement.REQUIRED,
        "Jill，一杯 Brandtini，照老样子来。",
    ),
    "stella": (
        "blight",
        ("spicy", "promo"),
        AlcoholRequirement.REQUIRED,
        "Jill，一杯 Bloom Light。冰别省掉。",
    ),
    "sei": (
        "moblast",
        ("sweet", "girly"),
        AlcoholRequirement.REQUIRED,
        "Jill，我想要一杯 Moonblast。照配方来就好。",
    ),
}


def order_for_customer(customer_id: str, event_id: int) -> DrinkOrder:
    try:
        drink_id, tags, alcohol, display_text = _ORDER_BLUEPRINTS[customer_id]
    except KeyError:
        raise ValueError("customer did not have a drink order blueprint") from None
    recipe = next(item for item in DRINK_RECIPES if item.drink_id == drink_id)
    return DrinkOrder(
        f"order_{event_id}",
        customer_id,
        drink_id,
        recipe.display_name,
        tags,
        alcohol,
        display_text,
    )
