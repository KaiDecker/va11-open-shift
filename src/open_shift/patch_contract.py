"""Validate patch metadata against a names-only game data inventory."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .game_data import GameDataInventory


_RESOURCE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,95}$")


class PatchContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PatchManifest:
    mod_id: str
    protocol_version: int
    supported_originals: tuple[dict[str, Any], ...]
    required_resources: tuple[str, ...]
    new_resources: tuple[str, ...]
    allowed_portraits: dict[str, str | None]
    return_target: str


def load_patch_manifest(path: str | Path) -> PatchManifest:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PatchContractError("patch manifest must be a JSON object")
    required_fields = {
        "mod_id",
        "protocol_version",
        "supported_originals",
        "required_resources",
        "new_resources",
        "allowed_portraits",
        "return_target",
    }
    if set(value) != required_fields:
        raise PatchContractError("patch manifest fields did not match the contract")
    if value["mod_id"] != "open_shift" or value["protocol_version"] != 1:
        raise PatchContractError("patch manifest identity or protocol was invalid")
    for field_name in ("required_resources", "new_resources"):
        items = value[field_name]
        if (
            not isinstance(items, list)
            or not items
            or not all(isinstance(item, str) and _RESOURCE_NAME.fullmatch(item) for item in items)
            or len(set(items)) != len(items)
        ):
            raise PatchContractError(f"{field_name} was invalid")
    if set(value["required_resources"]) & set(value["new_resources"]):
        raise PatchContractError("new resources collided with required resources")
    supported = value["supported_originals"]
    if not isinstance(supported, list) or not supported:
        raise PatchContractError("supported_originals was invalid")
    portraits = value["allowed_portraits"]
    if (
        not isinstance(portraits, dict)
        or not portraits
        or not all(
            isinstance(key, str)
            and _RESOURCE_NAME.fullmatch(key)
            and (resource is None or isinstance(resource, str))
            for key, resource in portraits.items()
        )
    ):
        raise PatchContractError("allowed_portraits was invalid")
    if value["return_target"] != "title":
        raise PatchContractError("return_target was invalid")
    return PatchManifest(
        mod_id=value["mod_id"],
        protocol_version=value["protocol_version"],
        supported_originals=tuple(supported),
        required_resources=tuple(value["required_resources"]),
        new_resources=tuple(value["new_resources"]),
        allowed_portraits=dict(portraits),
        return_target=value["return_target"],
    )


def validate_patch_target(
    manifest: PatchManifest, inventory: GameDataInventory
) -> None:
    matching = [
        baseline
        for baseline in manifest.supported_originals
        if baseline.get("data_win_sha256") == inventory.sha256
        and baseline.get("data_win_size") == inventory.file_size
    ]
    if not matching:
        raise PatchContractError("data.win was not a supported original baseline")
    available = set(inventory.resource_names)
    missing = sorted(set(manifest.required_resources) - available)
    if missing:
        raise PatchContractError(
            f"required game resources were missing: {', '.join(missing)}"
        )
    collisions = sorted(set(manifest.new_resources) & available)
    if collisions:
        raise PatchContractError(
            f"patch resource names already existed: {', '.join(collisions)}"
        )


def validate_gml_safety(source: str) -> None:
    normalized = source.lower()
    banned = (
        "execute_string",
        "shell_execute",
        "file_delete",
        "directory_destroy",
        "environment_get_variable",
        "network_create_server",
    )
    present = [name for name in banned if name in normalized]
    if present:
        raise PatchContractError(
            f"GML contained banned capabilities: {', '.join(present)}"
        )


def validate_patch_source_tree(path: str | Path) -> tuple[Path, ...]:
    """Validate every committed GML source and the expected event set."""

    directory = Path(path)
    expected = {
        "ag_open_shift_button_create.gml",
        "ag_open_shift_button_step.gml",
        "ag_open_shift_button_draw.gml",
        "ag_bridge_controller_create.gml",
        "ag_bridge_controller_step.gml",
        "ag_bridge_controller_http.gml",
        "ag_safe_text_create.gml",
        "ag_safe_text_draw.gml",
    }
    sources = tuple(sorted(directory.glob("*.gml")))
    if {source.name for source in sources} != expected:
        raise PatchContractError("GML patch source files did not match the contract")
    for source in sources:
        validate_gml_safety(source.read_text(encoding="utf-8"))
    combined = "\n".join(source.read_text(encoding="utf-8") for source in sources)
    required_boundaries = (
        "http://127.0.0.1:",
        "X-Open-Shift-Token",
        "open-shift-runtime.ini",
        "json_decode",
        "draw_text_ext",
        "out_to_title",
    )
    missing = [item for item in required_boundaries if item not in combined]
    if missing:
        raise PatchContractError(
            f"GML patch safety boundary was incomplete: {', '.join(missing)}"
        )
    return sources
