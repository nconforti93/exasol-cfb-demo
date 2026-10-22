import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from vs.cfbd_vs import replay
from vs.cfbd_vs.replay import (
    CorruptRecordingError,
    LocalFileSystem,
    MissingRecordingError,
    Recording,
    ReplayError,
    ReplayStore,
)

BASE = "/buckets/bfsdefault/default/cfbd/replay"
SLICE = "/games?seasonType=both&week=8&year=2024"
AT = datetime(2026, 10, 21, 12, 30, 15, 123000, tzinfo=UTC)


def digest(slice_key: str) -> str:
    return hashlib.sha256(slice_key.encode("utf-8")).hexdigest()[:24]


def make_recording(
    slice_key: str = SLICE, body: str = '[{"id": 1}]', attempts: int | None = 1
) -> Recording:
    return Recording(
        slice_key=slice_key, status=200, ms=412, requested_at=AT, body=body, attempts=attempts
    )


def older_format(slice_key: str = SLICE) -> str:
    """A recording file written before recordings stored the attempts."""
    data = json.loads(replay.encode_recording(make_recording(slice_key)))
    del data["attempts"]
    return json.dumps(data)


class MemoryFileSystem:
    def __init__(self, files: dict[str, str] | None = None, error: OSError | None = None) -> None:
        self.files = dict(files or {})
        self.error = error
        self.reads: list[str] = []

    def read_text(self, path: str) -> str:
        self.reads.append(path)
        if self.error is not None:
            raise self.error
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write_text(self, path: str, text: str) -> None:
        self.files[path] = text


def test_default_replay_path_is_the_bucketfs_default_bucket_location() -> None:
    assert replay.DEFAULT_REPLAY_PATH == "/buckets/bfsdefault/default/cfbd/replay"


def test_relative_path_is_endpoint_segments_then_hash_of_the_slice_key() -> None:
    assert replay.relative_path(SLICE) == f"games/{digest(SLICE)}.cfbd.json"


def test_nested_endpoint_paths_become_nested_directories() -> None:
    key = "/games/401628374/preview"
    assert replay.relative_path(key) == f"games/401628374/preview/{digest(key)}.cfbd.json"


def test_the_query_string_is_not_part_of_the_directory() -> None:
    first = replay.relative_path("/games?year=2019")
    second = replay.relative_path("/games?year=2020")
    assert first.rsplit("/", 1)[0] == second.rsplit("/", 1)[0] == "games"
    assert first != second


def test_the_same_slice_key_always_maps_to_the_same_file() -> None:
    assert replay.relative_path(SLICE) == replay.relative_path(SLICE)


def test_percent_encoded_path_segments_are_made_filesystem_safe() -> None:
    key = "/teams/Ohio%20State/x"
    assert replay.relative_path(key) == f"teams/Ohio_20State/x/{digest(key)}.cfbd.json"


@pytest.mark.parametrize("key", ["/../etc/passwd", "/a/../../b", "/./x", "/a//b"])
def test_relative_paths_never_leave_the_replay_directory(key: str) -> None:
    path = replay.relative_path(key)
    assert not path.startswith("/")
    assert ".." not in path.split("/")
    assert "." not in path.split("/")[:-1]
    assert "" not in path.split("/")


def test_a_root_slice_has_no_directory() -> None:
    assert replay.relative_path("/") == f"{digest('/')}.cfbd.json"


def test_recording_path_joins_the_base_path() -> None:
    expected = f"{BASE}/games/{digest(SLICE)}.cfbd.json"
    assert replay.recording_path(BASE, SLICE) == expected
    assert replay.recording_path(BASE + "/", SLICE) == expected


def test_encoded_recording_has_exactly_the_documented_fields() -> None:
    data = json.loads(replay.encode_recording(make_recording(attempts=3)))
    assert list(data) == ["slice_key", "status", "attempts", "ms", "requested_at", "body"]
    assert data["attempts"] == 3
    assert data["requested_at"] == "2026-10-21T12:30:15.123Z"
    assert data["body"] == '[{"id": 1}]'


def test_encode_then_decode_round_trips() -> None:
    recording = make_recording(body='{"unicode": "é\\n"}')
    text = replay.encode_recording(recording)
    assert replay.decode_recording(text, SLICE, "p") == recording


def test_unknown_attempts_are_encoded_as_null_and_round_trip() -> None:
    recording = make_recording(attempts=None)
    text = replay.encode_recording(recording)
    assert json.loads(text)["attempts"] is None
    assert replay.decode_recording(text, SLICE, "p") == recording


def test_a_recording_in_the_older_format_decodes_with_null_attempts() -> None:
    decoded = replay.decode_recording(older_format(), SLICE, "p")
    assert decoded.attempts is None
    assert decoded == make_recording(attempts=None)


def test_attempts_default_to_unknown() -> None:
    assert Recording(SLICE, 200, 1, AT, "[]").attempts is None


@pytest.mark.parametrize("attempts", ["2", True, 1.5, [1]])
def test_attempts_that_are_not_an_integer_are_corrupt(attempts: Any) -> None:
    data = json.loads(replay.encode_recording(make_recording()))
    data["attempts"] = attempts
    with pytest.raises(CorruptRecordingError, match="'attempts' is not an integer"):
        replay.decode_recording(json.dumps(data), SLICE, "p")


def test_naive_request_times_are_taken_as_utc() -> None:
    naive = Recording(SLICE, 200, 1, datetime(2026, 1, 2, 3, 4, 5), "[]")
    data = json.loads(replay.encode_recording(naive))
    assert data["requested_at"] == "2026-01-02T03:04:05.000Z"


def test_load_returns_the_recorded_status_latency_time_and_body() -> None:
    path = replay.recording_path(BASE, SLICE)
    files = {path: replay.encode_recording(make_recording())}
    store = ReplayStore(BASE, MemoryFileSystem(files))
    loaded = store.load(SLICE)
    assert (loaded.status, loaded.attempts, loaded.ms, loaded.requested_at) == (200, 1, 412, AT)
    assert loaded.body == '[{"id": 1}]'
    assert loaded.requested_at.tzinfo is UTC


def test_missing_recording_error_names_the_slice_the_replay_path_and_the_file() -> None:
    store = ReplayStore(BASE, MemoryFileSystem())
    with pytest.raises(MissingRecordingError) as error:
        store.load("/games?year=2023")
    message = str(error.value)
    assert "/games?year=2023" in message
    assert BASE in message
    assert replay.recording_path(BASE, "/games?year=2023") in message
    assert isinstance(error.value, ReplayError)


def test_a_missing_recording_does_not_touch_anything_but_its_own_path() -> None:
    files = MemoryFileSystem()
    ReplayStore(BASE, files).exists(SLICE)
    assert files.reads == [replay.recording_path(BASE, SLICE)]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("not json", "not valid JSON"),
        ("[1]", "not a JSON object"),
        ('{"slice_key": "/games?year=2024"}', "missing field"),
        (
            json.dumps(
                {
                    "slice_key": SLICE,
                    "status": "200",
                    "ms": 1,
                    "requested_at": "2026-01-01T00:00:00Z",
                    "body": "",
                }
            ),
            "status",
        ),
        (
            json.dumps(
                {
                    "slice_key": SLICE,
                    "status": 200,
                    "ms": 1,
                    "requested_at": "yesterday",
                    "body": "",
                }
            ),
            "requested_at",
        ),
        (
            json.dumps(
                {
                    "slice_key": SLICE,
                    "status": 200,
                    "ms": 1,
                    "requested_at": "2026-01-01T00:00:00Z",
                    "body": 5,
                }
            ),
            "body",
        ),
        (
            json.dumps(
                {
                    "slice_key": "/other",
                    "status": 200,
                    "ms": 1,
                    "requested_at": "2026-01-01T00:00:00Z",
                    "body": "",
                }
            ),
            "/other",
        ),
    ],
)
def test_corrupt_recordings_fail_clearly_with_the_file_path(text: str, reason: str) -> None:
    path = replay.recording_path(BASE, SLICE)
    store = ReplayStore(BASE, MemoryFileSystem({path: text}))
    with pytest.raises(CorruptRecordingError) as error:
        store.load(SLICE)
    assert path in str(error.value)
    assert reason in str(error.value)


def test_unreadable_files_raise_a_replay_error_naming_the_path() -> None:
    store = ReplayStore(BASE, MemoryFileSystem(error=PermissionError("denied")))
    with pytest.raises(ReplayError, match="denied") as error:
        store.load(SLICE)
    assert not isinstance(error.value, MissingRecordingError)
    assert replay.recording_path(BASE, SLICE) in str(error.value)


def test_exists_reports_whether_a_recording_is_there() -> None:
    path = replay.recording_path(BASE, SLICE)
    store = ReplayStore(BASE, MemoryFileSystem({path: replay.encode_recording(make_recording())}))
    assert store.exists(SLICE)
    assert not store.exists("/games?year=1999")


def test_save_writes_the_encoded_recording_and_returns_its_path() -> None:
    files = MemoryFileSystem()
    store = ReplayStore(BASE, files)
    path = store.save(make_recording())
    assert path == replay.recording_path(BASE, SLICE)
    assert files.files[path] == replay.encode_recording(make_recording())


def test_local_file_system_round_trip_creates_directories_and_leaves_no_temp_files(
    tmp_path: Path,
) -> None:
    store = ReplayStore(str(tmp_path / "replay"), LocalFileSystem())
    path = store.save(make_recording("/games/42/preview"))
    assert Path(path).is_file()
    assert store.load("/games/42/preview") == make_recording("/games/42/preview")
    assert [p.name for p in Path(path).parent.iterdir()] == [Path(path).name]


def test_local_file_system_raises_file_not_found_for_missing_files(tmp_path: Path) -> None:
    store = ReplayStore(str(tmp_path), LocalFileSystem())
    with pytest.raises(MissingRecordingError):
        store.load(SLICE)


def test_local_file_system_overwrites_an_existing_recording(tmp_path: Path) -> None:
    store = ReplayStore(str(tmp_path), LocalFileSystem())
    store.save(make_recording(body="[1]"))
    store.save(make_recording(body="[2]"))
    assert store.load(SLICE).body == "[2]"


def test_the_base_path_is_a_parameter_not_a_constant(tmp_path: Path) -> None:
    first = ReplayStore(str(tmp_path / "a"), LocalFileSystem())
    second = ReplayStore(str(tmp_path / "b"), LocalFileSystem())
    first.save(make_recording())
    assert not second.exists(SLICE)


def test_split_parts_keeps_small_bodies_in_one_part() -> None:
    assert replay.split_parts("[1,2]") == ["[1,2]"]


def test_split_parts_of_an_empty_body_is_one_empty_part() -> None:
    assert replay.split_parts("") == [""]


def test_split_parts_cuts_at_the_limit_and_loses_nothing() -> None:
    body = "x" * 5_000_001
    parts = replay.split_parts(body)
    assert [len(part) for part in parts] == [2_000_000, 2_000_000, 1_000_001]
    assert "".join(parts) == body


def test_split_parts_counts_characters_not_bytes() -> None:
    parts = replay.split_parts("é" * 7, limit=3)
    assert parts == ["ééé", "ééé", "é"]


def test_an_exact_multiple_of_the_limit_has_no_empty_trailing_part() -> None:
    assert replay.split_parts("ab" * 3, limit=2) == ["ab", "ab", "ab"]


def raw_rows(
    body: str, limit: int = 4, slice_key: str = SLICE, status: int = 200
) -> list[dict[str, Any]]:
    parts = replay.split_parts(body, limit=limit)
    return [
        {
            "SLICE_KEY": slice_key,
            "PART_NO": number,
            "PART_COUNT": len(parts),
            "BODY_PART": part,
            "HTTP_STATUS": status,
            "ATTEMPTS": 1,
            "MS": 412,
            "REQUESTED_AT": AT,
        }
        for number, part in enumerate(parts, start=1)
    ]


def test_parts_are_numbered_from_one() -> None:
    assert replay.FIRST_PART_NO == 1


def test_assemble_parts_reassembles_the_body_in_part_order() -> None:
    rows = raw_rows('{"a": "bcdefghij"}')
    rows.reverse()
    assert replay.assemble_parts(rows) == make_recording(body='{"a": "bcdefghij"}')


def test_assemble_parts_of_a_single_part() -> None:
    assert replay.assemble_parts(raw_rows("[]", limit=100)).body == "[]"


def test_split_assemble_encode_decode_round_trips_a_large_body() -> None:
    body = json.dumps([{"i": i, "s": "é" * 10} for i in range(2000)])
    recording = replay.assemble_parts(raw_rows(body, limit=4096))
    assert replay.decode_recording(replay.encode_recording(recording), SLICE, "p").body == body


def test_assemble_parts_treats_naive_request_times_as_utc() -> None:
    rows = raw_rows("[]", limit=100)
    rows[0]["REQUESTED_AT"] = datetime(2026, 10, 21, 12, 30, 15, 123000)
    assert replay.assemble_parts(rows).requested_at == AT


def test_assemble_parts_accepts_iso_text_request_times() -> None:
    rows = raw_rows("[]", limit=100)
    rows[0]["REQUESTED_AT"] = "2026-10-21 12:30:15.123"
    assert replay.assemble_parts(rows).requested_at == AT


def test_assemble_parts_rejects_no_rows() -> None:
    with pytest.raises(ReplayError, match="no raw parts"):
        replay.assemble_parts([])


def test_assemble_parts_names_the_missing_part() -> None:
    rows = raw_rows("abcdefghijkl")
    del rows[1]
    with pytest.raises(ReplayError, match=r"missing part\(s\) 2") as error:
        replay.assemble_parts(rows)
    assert SLICE in str(error.value)


def test_assemble_parts_rejects_duplicate_parts() -> None:
    rows = raw_rows("abcdefgh")
    rows.append(dict(rows[0]))
    with pytest.raises(ReplayError, match="duplicate part 1"):
        replay.assemble_parts(rows)


def test_assemble_parts_rejects_mixed_slices() -> None:
    rows = raw_rows("abcdefgh")
    rows[1]["SLICE_KEY"] = "/other"
    with pytest.raises(ReplayError, match="more than one slice"):
        replay.assemble_parts(rows)


def test_assemble_parts_rejects_inconsistent_part_counts() -> None:
    rows = raw_rows("abcdefgh")
    rows[1]["PART_COUNT"] = 5
    with pytest.raises(ReplayError, match="PART_COUNT"):
        replay.assemble_parts(rows)


def test_assemble_parts_rejects_inconsistent_status() -> None:
    rows = raw_rows("abcdefgh")
    rows[1]["HTTP_STATUS"] = 500
    with pytest.raises(ReplayError, match="HTTP_STATUS"):
        replay.assemble_parts(rows)


def test_assemble_parts_keeps_the_attempts_of_the_raw_output() -> None:
    rows = raw_rows("abcdefgh")
    for row in rows:
        row["ATTEMPTS"] = 3
    assert replay.assemble_parts(rows).attempts == 3


def test_assemble_parts_without_an_attempts_column_gives_unknown_attempts() -> None:
    rows = raw_rows("abcdefgh")
    for row in rows:
        del row["ATTEMPTS"]
    assert replay.assemble_parts(rows).attempts is None


def test_assemble_parts_rejects_inconsistent_attempts() -> None:
    rows = raw_rows("abcdefgh")
    rows[1]["ATTEMPTS"] = 2
    with pytest.raises(ReplayError, match="ATTEMPTS"):
        replay.assemble_parts(rows)


def test_assemble_parts_rejects_part_numbers_outside_the_count() -> None:
    rows = raw_rows("abcdefgh")
    rows[1]["PART_NO"] = 9
    with pytest.raises(ReplayError, match="part 9"):
        replay.assemble_parts(rows)
