"""Replay store: recorded CFBD responses, one file per slice key (stdlib only).

Layout: `<REPLAY_PATH>/<endpoint path segments>/<sha256(slice_key)[:24]>.cfbd.json`, holding
`{"slice_key", "status", "attempts", "ms", "requested_at", "body"}`. The body is the raw response
text, so every table of an endpoint replays from the same recording. `attempts` is the HTTP
attempts of the recorded request; recordings written before it existed lack the field and decode
with `attempts` None (unknown), never 0. On Exasol Personal, the BucketFS
host directory `.../runtime/exa/bucketfs/<svc>/<bkt>/<path>` appears in UDFs as
`/buckets/<svc>/<bkt>/<path>`, hence the default below.

The raw output of the fetch UDF emits a body in parts of at most `MAX_PART_CHARS` characters,
numbered from `FIRST_PART_NO`. `split_parts` cuts them and `assemble_parts` puts them together.
"""

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

DEFAULT_REPLAY_PATH = "/buckets/bfsdefault/default/cfbd/replay"
RECORDING_SUFFIX = ".cfbd.json"
DIGEST_LENGTH = 24
MAX_PART_CHARS = 2_000_000
FIRST_PART_NO = 1
MAX_SEGMENT_CHARS = 100
RECORDING_FIELDS = ("slice_key", "status", "ms", "requested_at", "body")
ATTEMPTS_FIELD = "attempts"

_UNSAFE_SEGMENT_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_DOT_SEGMENTS = (".", "..")


class ReplayError(Exception):
    """A recording cannot be read, decoded or assembled."""


class MissingRecordingError(ReplayError):
    """No recording exists for a slice key."""


class CorruptRecordingError(ReplayError):
    """A recording file exists but does not hold a valid recording."""


@dataclass(frozen=True)
class Recording:
    """One recorded response. `requested_at` is timezone-aware UTC; `attempts` None is unknown."""

    slice_key: str
    status: int
    ms: int
    requested_at: datetime
    body: str
    attempts: int | None = None


class FileSystem(Protocol):
    """The file access the store needs. `read_text` raises `FileNotFoundError` when absent."""

    def read_text(self, path: str) -> str: ...

    def write_text(self, path: str, text: str) -> None: ...


class LocalFileSystem:
    """Files on local disk. Writes create directories and replace the target atomically."""

    def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    def write_text(self, path: str, text: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)


def relative_path(slice_key: str) -> str:
    """Path of a slice's recording below the replay path, always inside it."""
    request_path = slice_key.split("?", 1)[0]
    directories = [_safe_segment(part) for part in request_path.split("/") if part]
    name = hashlib.sha256(slice_key.encode("utf-8")).hexdigest()[:DIGEST_LENGTH] + RECORDING_SUFFIX
    return "/".join([*directories, name])


def recording_path(base_path: str, slice_key: str) -> str:
    """Absolute path of a slice's recording under `base_path`."""
    return f"{base_path.rstrip('/')}/{relative_path(slice_key)}"


def encode_recording(recording: Recording) -> str:
    """The JSON text of a recording file."""
    return json.dumps(
        {
            "slice_key": recording.slice_key,
            "status": recording.status,
            ATTEMPTS_FIELD: recording.attempts,
            "ms": recording.ms,
            "requested_at": _format_time(recording.requested_at),
            "body": recording.body,
        },
        ensure_ascii=False,
    )


def decode_recording(text: str, slice_key: str, path: str) -> Recording:
    """Parse a recording file read from `path` for `slice_key`, validating every field."""
    try:
        data = json.loads(text)
    except ValueError:
        raise _corrupt(path, "it is not valid JSON") from None
    if not isinstance(data, dict):
        raise _corrupt(path, "it is not a JSON object")
    for field in RECORDING_FIELDS:
        if field not in data:
            raise _corrupt(path, f"missing field {field!r}")
    if data["slice_key"] != slice_key:
        raise _corrupt(path, f"it holds slice {data['slice_key']!r}, expected {slice_key!r}")
    for field in ("status", "ms"):
        if not _is_integer(data[field]):
            raise _corrupt(path, f"field {field!r} is not an integer")
    attempts = data.get(ATTEMPTS_FIELD)
    if attempts is not None and not _is_integer(attempts):
        raise _corrupt(path, f"field {ATTEMPTS_FIELD!r} is not an integer")
    if not isinstance(data["body"], str):
        raise _corrupt(path, "field 'body' is not a string")
    try:
        requested_at = _to_utc(data["requested_at"])
    except (TypeError, ValueError):
        raise _corrupt(path, "field 'requested_at' is not an ISO-8601 timestamp") from None
    return Recording(
        slice_key, data["status"], data["ms"], requested_at, data["body"], attempts=attempts
    )


class ReplayStore:
    """Reads and writes recordings under one base path."""

    def __init__(self, base_path: str, files: FileSystem | None = None) -> None:
        self._base_path = base_path
        self._files: FileSystem = files or LocalFileSystem()

    def load(self, slice_key: str) -> Recording:
        """The recording of `slice_key`, or an error naming the slice and where it looked."""
        path = recording_path(self._base_path, slice_key)
        try:
            text = self._files.read_text(path)
        except FileNotFoundError:
            raise MissingRecordingError(
                f"no recording for slice {slice_key!r} in replay path {self._base_path!r} "
                f"(expected {path}). Record it first, or switch FETCH_MODE to live."
            ) from None
        except OSError as error:
            raise ReplayError(
                f"cannot read the recording of slice {slice_key!r} at {path}: {error}"
            ) from None
        return decode_recording(text, slice_key, path)

    def exists(self, slice_key: str) -> bool:
        """Whether a recording of `slice_key` can be read."""
        try:
            self.load(slice_key)
        except MissingRecordingError:
            return False
        return True

    def save(self, recording: Recording) -> str:
        """Write a recording, replacing an earlier one, and return its path."""
        path = recording_path(self._base_path, recording.slice_key)
        self._files.write_text(path, encode_recording(recording))
        return path


def split_parts(body: str, limit: int = MAX_PART_CHARS) -> list[str]:
    """Cut `body` into parts of at most `limit` characters. An empty body is one empty part."""
    if not body:
        return [""]
    return [body[start : start + limit] for start in range(0, len(body), limit)]


def assemble_parts(rows: Iterable[Mapping[str, Any]]) -> Recording:
    """Rebuild a recording from the rows of the fetch UDF's raw output (one slice)."""
    parts = list(rows)
    if not parts:
        raise ReplayError("no raw parts to assemble")
    first = parts[0]
    slice_key = first["SLICE_KEY"]
    count = first["PART_COUNT"]
    requested_at = _to_utc(first["REQUESTED_AT"])
    bodies: dict[int, str] = {}
    for part in parts:
        _require_same_recording(part, first, requested_at)
        number = part["PART_NO"]
        if not FIRST_PART_NO <= number < FIRST_PART_NO + count:
            raise ReplayError(f"slice {slice_key!r}: part {number} is outside 1..{count}")
        if number in bodies:
            raise ReplayError(f"slice {slice_key!r}: duplicate part {number}")
        bodies[number] = part["BODY_PART"]
    missing = [n for n in range(FIRST_PART_NO, FIRST_PART_NO + count) if n not in bodies]
    if missing:
        listed = ", ".join(str(n) for n in missing)
        raise ReplayError(f"slice {slice_key!r}: missing part(s) {listed} of {count}")
    body = "".join(bodies[n] for n in sorted(bodies))
    return Recording(
        slice_key,
        first["HTTP_STATUS"],
        first["MS"],
        requested_at,
        body,
        attempts=first.get("ATTEMPTS"),
    )


def _require_same_recording(
    part: Mapping[str, Any], first: Mapping[str, Any], at: datetime
) -> None:
    if part["SLICE_KEY"] != first["SLICE_KEY"]:
        raise ReplayError("raw parts belong to more than one slice")
    for column in ("PART_COUNT", "HTTP_STATUS", "ATTEMPTS", "MS"):
        if part.get(column) != first.get(column):
            raise ReplayError(
                f"slice {first['SLICE_KEY']!r}: {column} differs between parts "
                f"({first.get(column)} vs {part.get(column)})"
            )
    if _to_utc(part["REQUESTED_AT"]) != at:
        raise ReplayError(f"slice {first['SLICE_KEY']!r}: REQUESTED_AT differs between parts")


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _safe_segment(segment: str) -> str:
    safe = _UNSAFE_SEGMENT_CHARS.sub("_", segment)[:MAX_SEGMENT_CHARS]
    return "_" if safe in _DOT_SEGMENTS else safe


def _corrupt(path: str, reason: str) -> CorruptRecordingError:
    return CorruptRecordingError(f"corrupt recording {path}: {reason}")


def _to_utc(value: datetime | str) -> datetime:
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return _to_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")
