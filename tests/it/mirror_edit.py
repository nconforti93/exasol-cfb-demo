"""Store an altered copy of stored mirror rows, and remove it again.

Read-only model modules (marker `model_readonly`) share one seeded league. A test that needs a row
stored twice (a later fetch, a changed value) adds the copy with `store_changed_copy` under a
marker slice key and removes it with `remove_copies`, so the shared league is unchanged afterwards.
Both rebuild the model's derived tables afterwards (decision [40]), as a load would.
"""

from __future__ import annotations

from collections.abc import Mapping

from tests.it.harness import Harness

COPY_TABLE = "MIRROR_EDIT_COPY"


def store_changed_copy(
    harness: Harness,
    table: str,
    where: str,
    *,
    marker: str,
    fetched_at: str,
    row_offset: int,
    changes: Mapping[str, str] | None = None,
) -> int:
    """Copy the rows of `CFB_CORE.<table>` matching `where` as a newer fetch.

    The copy has `REQ_SLICE` = `marker`, `REQ_AT` = `fetched_at` (a timestamp literal body),
    `ROW_NO` raised by `row_offset`, and the SQL expressions of `changes` assigned to their
    columns. Returns the number of rows stored.
    """
    scratch = f"{harness.core}.{COPY_TABLE}"
    assignments = {
        "ROW_NO": f"ROW_NO + {row_offset}",
        "REQ_SLICE": f"'{marker}'",
        "REQ_AT": f"TIMESTAMP '{fetched_at}'",
        **(changes or {}),
    }
    sets = ", ".join(f"{name} = {expression}" for name, expression in assignments.items())
    connection = harness.owner
    connection.execute(
        f"CREATE OR REPLACE TABLE {scratch} AS SELECT * FROM {harness.core}.{table} WHERE {where}"
    )
    try:
        connection.execute(f"UPDATE {scratch} SET {sets}")
        connection.execute(f"INSERT INTO {harness.core}.{table} SELECT * FROM {scratch}")
        stored = connection.execute(f"SELECT COUNT(*) FROM {scratch}").fetchone()
    finally:
        connection.execute(f"DROP TABLE IF EXISTS {scratch}")
    harness.rebuild_model()
    return int(stored[0])


def remove_copies(harness: Harness, table: str, marker: str) -> None:
    """Delete the copies stored under `marker`."""
    harness.owner.execute(f"DELETE FROM {harness.core}.{table} WHERE REQ_SLICE = '{marker}'")
    harness.rebuild_model()
