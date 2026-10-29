"""The UDF wrapper of `CFB_INFRA.SIM_ENGINE`: read the SET group, run the engine, emit one frame.

`engine/bundle.py` registers this module and `engine.core` in the script under their names, and the
script's entry function calls `run(ctx, exa)`; on the host the tests call it with fakes. The wrapper
does only what needs the script runtime: the chunked read of the group (`ctx.get_dataframe`), the
instance identity (`exa.meta`) and one `ctx.emit` of a DataFrame in the stage's column order. The
engine itself (`core.run_batch`) parses, validates and simulates.

The group arrives in chunks of `CHUNK_ROWS` rows, so no call holds the whole model twice. pandas is
imported inside `run`: the script runtime has it, the host does not need it.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

import numpy as np

from engine import core

CHUNK_ROWS = 4096


def read_columns(ctx: Any, chunk_rows: int = CHUNK_ROWS) -> dict[str, core.Array]:
    """The whole SET group by column name, read with `ctx.get_dataframe` in chunks."""
    parts: dict[str, list[core.Array]] = {}
    while True:
        frame = ctx.get_dataframe(num_rows=chunk_rows)
        if frame is None or len(frame) == 0:
            break
        for name in frame.columns:
            parts.setdefault(str(name), []).append(np.asarray(frame[name].to_numpy()))
        if len(frame) < chunk_rows:
            break
    return {name: np.concatenate(chunks) for name, chunks in parts.items()}


def instance_identity(exa: Any) -> tuple[str, str]:
    """The UDF instance (`vm_id`) and database node that run this call, as text."""
    return str(exa.meta.vm_id), str(exa.meta.node_id)


def run(ctx: Any, exa: Any) -> None:
    """One engine call: read the group, simulate the batch, emit the `SIM_STAGE` rows as a frame."""
    pandas = import_module("pandas")
    columns = read_columns(ctx)
    vm, node = instance_identity(exa)
    stage = core.run_batch(columns, vm, node)
    frame = pandas.DataFrame({name: stage[name] for name, _ in core.STAGE_COLUMNS})
    ctx.emit(frame)
