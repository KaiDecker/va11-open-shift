"""Small, self-contained binary deltas for GameMaker ``data.win`` files.

The player package cannot include UTMT or a second copy of ``data.win``. A
delta stores content-defined spans: spans identical to the verified original
are references into that file; changed spans are zlib-compressed in the
delta. Content-defined boundaries survive inserted or removed bytes before an
unchanged region.
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import zlib
from pathlib import Path

from .game_data import GameDataError, _read_chunks


class DataDeltaError(ValueError):
    """Raised when a delta is invalid, unsafe, or inefficient."""


_MAGIC = b"OSDELTA2"
_VERSION = 2
_HEADER = struct.Struct("<8sIQQ32s32sI")
# opcode, source offset, uncompressed output size, payload size. A COPY
# record has no payload; a DATA record contains zlib bytes or a literal.
_RECORD = struct.Struct("<B7xQQQ")
_COPY = 0
_DATA = 1
_LITERAL = 2
_MAX_CHUNKS = 100_000
_MIN_CHUNK = 16 * 1024
_MAX_CHUNK = 256 * 1024
_MASK = (1 << 16) - 1
_U64_MASK = (1 << 64) - 1
_HASH_WINDOW = 64
_HASH_BASE = 0x9E3779B185EBCA87
_HASH_POWER = pow(_HASH_BASE, _HASH_WINDOW - 1, 1 << 64)


def _sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def _parse(path: Path) -> bytes:
    raw = path.read_bytes()
    try:
        _read_chunks(raw)
    except (GameDataError, ValueError) as exc:
        raise DataDeltaError(f"{path} was not a valid FORM data.win: {exc}") from exc
    return raw


def _content_spans(data: bytes) -> list[tuple[int, int]]:
    """Return deterministic content-defined spans for a byte sequence."""

    if not data:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    rolling = 0
    for offset, byte in enumerate(data):
        # A rolling polynomial hash is independent of the previous chunk
        # boundary. This is what lets a shifted region rejoin the baseline.
        if offset < _HASH_WINDOW:
            rolling = (rolling * _HASH_BASE + byte) & _U64_MASK
        else:
            rolling = (
                (rolling - data[offset - _HASH_WINDOW] * _HASH_POWER) * _HASH_BASE + byte
            ) & _U64_MASK
        length = offset + 1 - start
        if length < _MIN_CHUNK:
            continue
        if (rolling & _MASK) == 0 or length >= _MAX_CHUNK:
            spans.append((start, offset + 1))
            start = offset + 1
    if start < len(data):
        spans.append((start, len(data)))
    return spans


def _chunk_key(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def create_delta(
    original_data_win: str | Path,
    patched_data_win: str | Path,
    output: str | Path,
    *,
    max_ratio: float = 0.80,
) -> dict[str, object]:
    """Create a content-defined delta and reject a near-full result."""

    original_path = Path(original_data_win).expanduser().resolve()
    patched_path = Path(patched_data_win).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if not original_path.is_file() or not patched_path.is_file():
        raise DataDeltaError("original and patched data.win files must exist")
    original = _parse(original_path)
    patched = _parse(patched_path)
    original_spans = _content_spans(original)
    patched_spans = _content_spans(patched)
    original_by_key: dict[bytes, list[tuple[int, int]]] = {}
    for start, end in original_spans:
        original_by_key.setdefault(_chunk_key(original[start:end]), []).append((start, end))

    records: list[tuple[int, int, int, bytes]] = []
    embedded_bytes = 0
    copy_count = 0
    for start, end in patched_spans:
        payload = patched[start:end]
        match = None
        for source_start, source_end in original_by_key.get(_chunk_key(payload), ()):
            if original[source_start:source_end] == payload:
                match = (source_start, source_end)
                break
        if match is not None:
            records.append((_COPY, match[0], len(payload), b""))
            copy_count += 1
            continue
        compressed = zlib.compress(payload, level=9)
        encoding = _DATA
        if len(compressed) >= len(payload):
            compressed = payload
            encoding = _LITERAL
        records.append((encoding, 0, len(payload), compressed))
        embedded_bytes += len(compressed)

    if not records or len(records) > _MAX_CHUNKS:
        raise DataDeltaError("data.win content table was invalid or too large")
    estimated = _HEADER.size + sum(_RECORD.size + len(data) for _, _, _, data in records)
    ratio = estimated / max(1, len(patched))
    if ratio >= max_ratio:
        raise DataDeltaError(
            f"delta would be too large ({estimated} bytes, {ratio:.1%} of patched data.win); "
            "refusing to ship a near-full-file patch"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    original_hash = _sha256(original_path)
    patched_hash = _sha256(patched_path)
    with output_path.open("wb") as handle:
        handle.write(_HEADER.pack(_MAGIC, _VERSION, len(original), len(patched), original_hash, patched_hash, len(records)))
        for encoding, source_offset, size, data in records:
            handle.write(_RECORD.pack(encoding, source_offset, size, len(data)))
            handle.write(data)
    return {
        "status": "created",
        "original_sha256": original_hash.hex(),
        "patched_sha256": patched_hash.hex(),
        "original_size": len(original),
        "patched_size": len(patched),
        "delta_size": output_path.stat().st_size,
        "ratio": ratio,
        "chunk_count": len(records),
        "copied_chunks": copy_count,
        "embedded_bytes": embedded_bytes,
    }


def apply_delta(
    original_data_win: str | Path,
    delta: str | Path,
    destination: str | Path,
) -> dict[str, object]:
    """Apply a verified delta atomically without modifying the source."""

    original_path = Path(original_data_win).expanduser().resolve()
    delta_path = Path(delta).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if not original_path.is_file() or not delta_path.is_file():
        raise DataDeltaError("original data.win and delta file must exist")
    original = _parse(original_path)
    source_hash = _sha256(original_path)
    with delta_path.open("rb") as handle:
        header = handle.read(_HEADER.size)
        if len(header) != _HEADER.size:
            raise DataDeltaError("delta header was truncated")
        magic, version, original_size, patched_size, expected_original, expected_patched, count = _HEADER.unpack(header)
        if magic != _MAGIC or version != _VERSION:
            raise DataDeltaError("delta format was unsupported")
        if original_size != len(original) or expected_original != source_hash:
            raise DataDeltaError("original data.win did not match the delta baseline")
        if count < 1 or count > _MAX_CHUNKS:
            raise DataDeltaError("delta content count was invalid")
        output = bytearray()
        for _ in range(count):
            raw_record = handle.read(_RECORD.size)
            if len(raw_record) != _RECORD.size:
                raise DataDeltaError("delta record was truncated")
            encoding, source_offset, size, data_size = _RECORD.unpack(raw_record)
            if size == 0 or data_size > patched_size or len(output) + size > patched_size:
                raise DataDeltaError("delta record size was invalid")
            if encoding == _COPY:
                if data_size != 0 or source_offset + size > len(original):
                    raise DataDeltaError("delta referenced an invalid original span")
                payload = original[source_offset : source_offset + size]
            elif encoding in (_DATA, _LITERAL):
                data = handle.read(data_size)
                if len(data) != data_size:
                    raise DataDeltaError("delta payload was truncated")
                if encoding == _DATA:
                    try:
                        payload = zlib.decompress(data)
                    except zlib.error as exc:
                        raise DataDeltaError("delta compressed payload was invalid") from exc
                else:
                    payload = data
            else:
                raise DataDeltaError("delta payload encoding was unsupported")
            if len(payload) != size:
                raise DataDeltaError("delta payload size did not match its record")
            output.extend(payload)
        if handle.read(1):
            raise DataDeltaError("delta contained trailing bytes")
    if len(output) != patched_size or hashlib.sha256(output).digest() != expected_patched:
        raise DataDeltaError("delta reconstructed data.win with an unexpected SHA256")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination_path.name}.", suffix=".tmp", dir=destination_path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(output)
        temporary.replace(destination_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "status": "applied",
        "original_sha256": source_hash.hex(),
        "patched_sha256": expected_patched.hex(),
        "destination": str(destination_path),
        "file_size": len(output),
    }
