"""One transaction over a pyexasol-like connection, for the loader's host code."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def run_transaction(connection: Any, statements: Sequence[str]) -> None:
    """Run `statements` in one transaction: COMMIT at the end, ROLLBACK on any failure.

    Autocommit is switched off for the statements (when the connection can) and on again after,
    also after a failure, which is re-raised after the rollback.
    """
    switch = getattr(connection, "set_autocommit", None)
    if switch is not None:
        switch(False)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        if switch is not None:
            switch(True)
