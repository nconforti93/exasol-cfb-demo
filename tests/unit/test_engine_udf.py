"""`engine/udf.py` over fakes: the chunked read of the SET group, the instance identity and the
one DataFrame emit. The engine itself is `tests/unit/test_engine_core.py`; the script in the
database is `tests/it/test_sim_engine_it.py`."""

from __future__ import annotations

import sys
import types
from typing import Any

import numpy as np
import pytest

from engine import core, udf


class FakeSeries:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def to_numpy(self) -> Any:
        return np.array(self._values, dtype=object)


class FakeFrame:
    def __init__(self, columns: dict[str, list[Any]]) -> None:
        self._columns = columns
        self.columns = list(columns)

    def __len__(self) -> int:
        return len(next(iter(self._columns.values())))

    def __getitem__(self, name: str) -> FakeSeries:
        return FakeSeries(self._columns[name])


class FakeContext:
    """A SET group of `rows` rows that hands out `get_dataframe(num_rows=n)` chunks."""

    def __init__(self, rows: int) -> None:
        self._rows = rows
        self._read = 0
        self.requests: list[int] = []
        self.emitted: list[Any] = []

    def get_dataframe(self, num_rows: int) -> FakeFrame | None:
        self.requests.append(num_rows)
        if self._read >= self._rows:
            return None
        count = min(num_rows, self._rows - self._read)
        rows = range(self._read, self._read + count)
        self._read += count
        return FakeFrame({"ROW_KIND": [f"k{i}" for i in rows], "TEAM_IDX": list(rows)})

    def emit(self, frame: Any) -> None:
        self.emitted.append(frame)


class FakeExa:
    def __init__(self) -> None:
        self.meta = types.SimpleNamespace(vm_id=987654321, node_id=3)


def test_the_group_is_read_in_chunks_until_a_short_chunk() -> None:
    ctx = FakeContext(5)
    columns = udf.read_columns(ctx, chunk_rows=2)
    assert ctx.requests == [2, 2, 2]
    assert columns["TEAM_IDX"].tolist() == [0, 1, 2, 3, 4]
    assert columns["ROW_KIND"].tolist() == ["k0", "k1", "k2", "k3", "k4"]


def test_a_group_that_ends_on_a_chunk_boundary_is_read_until_none() -> None:
    ctx = FakeContext(4)
    columns = udf.read_columns(ctx, chunk_rows=2)
    assert ctx.requests == [2, 2, 2]
    assert columns["TEAM_IDX"].tolist() == [0, 1, 2, 3]


def test_the_default_chunk_is_4096_rows() -> None:
    ctx = FakeContext(1)
    udf.read_columns(ctx)
    assert ctx.requests == [4096]
    assert udf.CHUNK_ROWS == 4096


def test_an_empty_group_gives_no_columns() -> None:
    assert udf.read_columns(FakeContext(0)) == {}


def test_the_identity_is_the_vm_and_the_node_as_text() -> None:
    assert udf.instance_identity(FakeExa()) == ("987654321", "3")


def test_run_emits_one_frame_in_the_stage_column_order(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def run_batch(columns: Any, vm: str, node: str) -> dict[str, core.Array]:
        seen.update(columns=columns, vm=vm, node=node)
        return {name: np.array([name], dtype=object) for name, _ in reversed(core.STAGE_COLUMNS)}

    class FakeFrameOut:
        def __init__(self, data: dict[str, Any]) -> None:
            self.data = data

    monkeypatch.setattr(core, "run_batch", run_batch)
    monkeypatch.setitem(sys.modules, "pandas", types.SimpleNamespace(DataFrame=FakeFrameOut))
    ctx = FakeContext(3)
    udf.run(ctx, FakeExa())
    (frame,) = ctx.emitted
    assert list(frame.data) == [name for name, _ in core.STAGE_COLUMNS]
    assert seen["vm"] == "987654321" and seen["node"] == "3"
    assert seen["columns"]["TEAM_IDX"].tolist() == [0, 1, 2]


def test_an_engine_error_propagates_without_an_emit(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_batch(columns: Any, vm: str, node: str) -> dict[str, core.Array]:
        raise core.ModelError("missing parameter game_sigma")

    monkeypatch.setattr(core, "run_batch", run_batch)
    monkeypatch.setitem(sys.modules, "pandas", types.SimpleNamespace(DataFrame=dict))
    ctx = FakeContext(1)
    with pytest.raises(core.ModelError, match="missing parameter game_sigma"):
        udf.run(ctx, FakeExa())
    assert ctx.emitted == []
