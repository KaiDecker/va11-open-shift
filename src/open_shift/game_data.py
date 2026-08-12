"""Read-only GameMaker data.win inventory for supported patch baselines."""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path


ORIGINAL_DATA_SHA256 = "f14c4443838179f633f362c6fa20ca849d479c555eb315a507b4165ffa940991"
ORIGINAL_DATA_SIZE = 221_895_676
_CHUNK_NAME = re.compile(rb"^[A-Z0-9 ]{4}$")
_RESOURCE_NAME = re.compile(rb"^[A-Za-z_][A-Za-z0-9_./-]{1,95}$")


class GameDataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GameDataChunk:
    name: str
    offset: int
    size: int


@dataclass(frozen=True, slots=True)
class GameDataInventory:
    path: str
    file_size: int
    sha256: str
    supported_original: bool
    chunks: tuple[GameDataChunk, ...]
    resource_names: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "supported_original": self.supported_original,
            "chunks": [asdict(chunk) for chunk in self.chunks],
            "resource_names": list(self.resource_names),
        }


def _read_chunks(data: bytes) -> tuple[GameDataChunk, ...]:
    if len(data) < 8 or data[:4] != b"FORM":
        raise GameDataError("file was not a GameMaker FORM container")
    declared_size = struct.unpack_from("<I", data, 4)[0]
    if declared_size + 8 != len(data):
        raise GameDataError("FORM size did not match the file length")
    chunks: list[GameDataChunk] = []
    offset = 8
    while offset < len(data):
        if offset + 8 > len(data):
            raise GameDataError("chunk header extended past end of file")
        raw_name = data[offset : offset + 4]
        if not _CHUNK_NAME.fullmatch(raw_name):
            raise GameDataError("chunk name was invalid")
        size = struct.unpack_from("<I", data, offset + 4)[0]
        payload_offset = offset + 8
        if payload_offset + size > len(data):
            raise GameDataError("chunk payload extended past end of file")
        chunks.append(
            GameDataChunk(raw_name.decode("ascii"), payload_offset, size)
        )
        offset = payload_offset + size
    return tuple(chunks)


def _extract_names(data: bytes, chunks: tuple[GameDataChunk, ...]) -> tuple[str, ...]:
    strings = next((chunk for chunk in chunks if chunk.name == "STRG"), None)
    if strings is None:
        raise GameDataError("STRG chunk was missing")
    payload = data[strings.offset : strings.offset + strings.size]
    names: set[str] = set()
    for raw in payload.split(b"\0"):
        if _RESOURCE_NAME.fullmatch(raw):
            names.add(raw.decode("ascii"))
    return tuple(sorted(names))


def inspect_game_data(path: str | Path) -> GameDataInventory:
    resolved = Path(path).resolve()
    data = resolved.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    chunks = _read_chunks(data)
    return GameDataInventory(
        path=str(resolved),
        file_size=len(data),
        sha256=digest,
        supported_original=(
            len(data) == ORIGINAL_DATA_SIZE and digest == ORIGINAL_DATA_SHA256
        ),
        chunks=chunks,
        resource_names=_extract_names(data, chunks),
    )


def inventory_json(inventory: GameDataInventory) -> str:
    return json.dumps(inventory.to_dict(), ensure_ascii=False, indent=2)


def compare_inventories(
    baseline: GameDataInventory, reference: GameDataInventory
) -> dict[str, object]:
    baseline_chunks = {chunk.name: chunk.size for chunk in baseline.chunks}
    reference_chunks = {chunk.name: chunk.size for chunk in reference.chunks}
    names = sorted(set(baseline_chunks) | set(reference_chunks))
    return {
        "baseline_sha256": baseline.sha256,
        "reference_sha256": reference.sha256,
        "file_size_delta": reference.file_size - baseline.file_size,
        "chunk_size_deltas": {
            name: reference_chunks.get(name, 0) - baseline_chunks.get(name, 0)
            for name in names
            if reference_chunks.get(name, 0) != baseline_chunks.get(name, 0)
        },
        "added_resource_names": sorted(
            set(reference.resource_names) - set(baseline.resource_names)
        ),
        "removed_resource_names": sorted(
            set(baseline.resource_names) - set(reference.resource_names)
        ),
    }
